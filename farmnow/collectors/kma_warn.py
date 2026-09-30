"""기상청 기상특보: 통보문(getWthrWrnMsg)과 현재 특보 현황(getPwnStatus).
data.go.kr 키(DATA_GO_KR_KEY)가 있을 때만 동작. 통보문의 '해당 구역'(t2)만 인용하고
유의사항(other)·예비특보(t7)는 쓰지 않는다(행동 지시·예보 문구를 만들지 않음)."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

import requests

from ..model import NewsItem
from ..rules import parse_status_t6, warning_kinds
from ..util import now_kst, KST

BASE = "https://apis.data.go.kr/1360000/WthrWrnInfoService"
REPORT_URL = ("https://www.weather.go.kr/w/special-report/list.do?prevStn=108&prevKind=met&stn=108&kind=met"
              "&date={date}&reportId=met:{tmfc}:{seq}")
REGION_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기",
    "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "제주도": "제주", "울릉도": "경북", "흑산도": "전남",
}
_SEA_RE = re.compile(r"먼바다|앞바다|해상|(?:남해|서해|동해)(?:동부|서부|중부|남부|북부)")
_PAREN_RE = re.compile(r"\([^()]*\)")


def _key(src: dict) -> str:
    key = os.environ.get(src.get("env_key", "DATA_GO_KR_KEY"))
    if not key:
        raise RuntimeError("no_api_key")
    return unquote(key) if "%" in key else key  # 포털의 Encoding 키를 넣은 경우 원래 값으로


def _call(op: str, key: str, **params) -> list[dict]:
    r = requests.get(f"{BASE}/{op}", params={"serviceKey": key, "pageNo": 1, "numOfRows": 100,
                                             "dataType": "JSON", "stnId": 108, **params}, timeout=30)
    r.raise_for_status()
    head = r.json()["response"]["header"]
    if head["resultCode"] == "03":      # NO_DATA
        return []
    if head["resultCode"] != "00":
        raise RuntimeError(f"KMA {head['resultCode']} {head.get('resultMsg', '')}")
    return r.json()["response"]["body"]["items"]["item"] or []


def _tm(v) -> datetime | None:
    try:
        return datetime.strptime(str(v), "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        return None


def regions_short(text: str) -> list[str]:
    out = []
    for full, short in REGION_SHORT.items():
        if full in text and short not in out:
            out.append(short)
    if _SEA_RE.search(_PAREN_RE.sub("", text)) and "해상" not in out:   # 괄호 안 시군 구역(동해평지·남해군)은 해역이 아니다
        out.append("해상")
    return out


def _actions(t2: str) -> list[tuple[str, str]]:
    """'(1) 호우주의보 발표 : 경상북도(경주동부)' 줄들을 (조치, 구역)으로."""
    out = []
    for line in (t2 or "").splitlines():
        line = re.sub(r"^\(\d+\)\s*", "", line.strip())
        if not line:
            continue
        act, _, reg = line.partition(":")
        out.append((act.strip(), reg.strip()))
    return out


def _effective(t3: str) -> dict[str, datetime]:
    """'(1) 풍랑주의보 해제 : 2026년 09월 26일 12시 00분' 줄들을 {조치: 발효 시각}으로."""
    out = {}
    for act, when in _actions(t3):
        m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2})시\s*(\d{1,2})분", when)
        if m:
            out[act] = datetime(*map(int, m.groups()), tzinfo=KST)
    return out


def collect(src: dict) -> tuple[list[NewsItem], dict | None]:
    key = _key(src)
    now = now_kst()
    rows = _call("getWthrWrnMsg", key,
                 fromTmFc=(now - timedelta(days=6)).strftime("%Y%m%d"),  # API 허용 최대 범위
                 toTmFc=now.strftime("%Y%m%d"))
    items: list[NewsItem] = []
    latest = None
    for it in rows:
        when = _tm(it.get("tmFc"))
        if not when:
            continue
        seq = it.get("tmSeq")
        title = (it.get("t1") or "").strip()
        acts = _actions(it.get("t2", ""))
        eff = _effective(it.get("t3", ""))                      # 조치별 발효 시각
        all_release = all(a.endswith("해제") for a, _ in acts) if acts else title.endswith("해제")
        later = [t for t in eff.values() if t and t > when]     # 발표보다 늦게 발효되는 조치
        released = all_release and not later
        live_text = " ".join(a for a, _ in acts if not a.endswith("해제")) or ("" if all_release else title)
        sev = "info" if all_release else "alert" if "경보" in live_text else "warning"
        regs = regions_short(" ".join(r for _, r in acts))
        no = f"제{when.month:02d}-{seq}호"
        head = f"기상청, {title}" + (f" — {'·'.join(regs[:4])}" if regs else "")
        body = " / ".join((f"{a}({eff[a]:%H:%M}부터)" if eff.get(a) and eff[a] > when else a) + (f": {r}" if r else "") for a, r in acts)
        if len(body) > 300:
            body = body[:297].rstrip() + "…"
        items.append(NewsItem(
            source_id=src["id"], source_name=src["name"], org=src["org"],
            url=REPORT_URL.format(date=when.strftime("%Y-%m-%d"), tmfc=when.strftime("%Y%m%d%H%M"), seq=seq),
            headline=head, event_time=when, time_precision="minute", fetched_at=now,
            category="weather", label="특보 해제" if released else "해제 예고" if all_release else "특보", license="kogl1", severity=sev,
            body=body, body_mode="excerpt", generator="rule", tags_events=["#기상특보"], tags_regions=regs,
            uid=f"{when:%Y%m%d%H%M}-{seq}", time_source="api", extra={"no": no, "kinds": sorted(warning_kinds(live_text))},
        ))
        if latest is None or (it.get("tmFc"), seq) > (latest.get("tmFc"), latest.get("tmSeq")):
            latest = it

    # 현재 발효 현황: 현황 API → 실패하면 가장 최근 통보문의 t6
    warn = None
    try:
        st = _call("getPwnStatus", key)
        if st:
            s = st[0]
            warn = {"as_of": (_tm(s.get("tmFc")) or now).isoformat(), "seq": s.get("tmSeq"),
                    "active": parse_status_t6(s.get("t6", "")), "source": "status", "checked_at": now.isoformat()}
    except Exception:
        warn = None
    if warn is None and latest is not None:
        warn = {"as_of": (_tm(latest.get("tmFc")) or now).isoformat(), "seq": latest.get("tmSeq"),
                "active": parse_status_t6(latest.get("t6", "")), "source": "bulletin", "checked_at": now.isoformat()}
    if warn:
        for a in warn["active"]:
            a["short"] = regions_short(a["regions"])
    return items, warn
