"""규칙 엔진: 태그 부착(사전 매칭), 오늘의 핵심 5, 태그 빈도, 특보 발효 상태, 24시간 요약."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from .model import NewsItem

_SEVERITY_W = {"alert": 4, "warning": 3, "advisory": 2, "info": 1}
_CAT_CAP = {"price": 1}          # 오늘의 핵심 5에서 카테고리별 최대 건수(기본 2)
_HANGUL = "가-힣"
_REGION_FULL = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
                "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원",
                "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
                "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주"}

# 특보 종류 정규화(경보·주의보 구분 유지)
_KIND_RE = re.compile(r"(강풍|풍랑|호우|대설|건조|폭풍해일|한파|태풍|황사|폭염|지진해일)(경보|주의보)")


def _mask(text: str, excludes: list[str]) -> str:
    for w in excludes:
        text = text.replace(w, " " * len(w))
    return text


def tag(item: NewsItem, dicts: dict) -> None:
    excludes = dicts.get("exclude", []) or []
    text = _mask(f"{item.headline} {item.body}", excludes)
    for name, aliases in (dicts.get("items") or {}).items():
        if name in item.tags_items:
            continue
        if any(a in text for a in aliases):
            item.tags_items.append(name)
    for ev, kws in (dicts.get("events") or {}).items():
        if ev not in item.tags_events and any(k in text for k in kws):
            item.tags_events.append(ev)
    head = _mask(item.headline, excludes)
    for full, short in _REGION_FULL.items():
        if full in head and short not in item.tags_regions:
            item.tags_regions.append(short)
    for rg in dicts.get("regions") or []:
        # 왼쪽에 한글이 붙지 않을 때만(예: '경기 침체'의 '경기'는 exclude로, '대전환'의 '대전'은 오른쪽 한글로 걸러지지 않으므로 exclude 사용)
        if rg not in item.tags_regions and re.search(rf"(?<![{_HANGUL}]){re.escape(rg)}", head):
            item.tags_regions.append(rg)


def warning_kinds(text: str) -> set[str]:
    return {m.group(0) for m in _KIND_RE.finditer(text or "")}


def parse_status_t6(t6: str) -> list[dict]:
    """기상청 통보문 t6('o 호우주의보 : 경상북도(경주동부)' 줄 단위, 없으면 'o 없 음')를 발효 목록으로."""
    out = []
    for line in (t6 or "").splitlines():
        line = line.strip().lstrip("o").strip()
        if not line or line.replace(" ", "") == "없음":
            continue
        kind, _, regions = line.partition(":")
        out.append({"kind": kind.strip(), "regions": regions.strip()})
    return out


def top5(items: list[NewsItem], now: datetime, active_kinds: set[str] | None = None) -> list[NewsItem]:
    """점수 = 심각도 × 신선도(24h 1.0 / 48h 0.6) × (1 + 0.2×태그 수). 날짜만 있는 항목은 등록일이 오늘·어제일 때만.
    기상특보는 해제 통보문을 제외하고, 발효 상태를 알 때는 발효 중인 종류만 후보로 둔다."""
    scored = []
    for it in items:
        if it.category == "weather":
            if it.label != "특보":
                continue
            if active_kinds is not None and not (set(it.extra.get("kinds") or warning_kinds(it.headline)) & active_kinds):
                continue
        age = now - it.event_time
        if it.time_precision == "date":
            if age > timedelta(days=2):
                continue
            fresh = 0.6
        else:
            fresh = 1.0 if age <= timedelta(hours=24) else 0.6 if age <= timedelta(hours=48) else 0
        if fresh == 0:
            continue
        ntags = len(it.tags_items) + len(it.tags_events) + len(it.tags_regions)
        scored.append((_SEVERITY_W[it.severity] * fresh * (1 + 0.2 * ntags), it))
    scored.sort(key=lambda x: (-x[0], -x[1].event_time.timestamp()))   # 동점이면 최신순
    picked, per_cat, used_tags = [], Counter(), set()
    for s, it in scored:
        if per_cat[it.category] >= _CAT_CAP.get(it.category, 2):
            continue
        topic = set(it.tags_events) - {"#기상특보"}
        if topic & used_tags:                      # 같은 주제(사건 태그)는 1건만
            continue
        picked.append(it)
        per_cat[it.category] += 1
        used_tags |= topic
        if len(picked) == 5:
            break
    return picked


def tag_trend(items: list[NewsItem], now: datetime) -> tuple[list[dict], int]:
    """최근 24→48→168시간 창에서 빈도 2 이상 태그가 3개 이상 되는 첫 창. 반환 [{label, q, n}]."""
    found = []
    for hours in (24, 48, 168):
        c = Counter()
        for it in items:
            if now - it.event_time <= timedelta(hours=hours):
                c.update(it.tags_items + it.tags_events)
        found.append(([(t, n) for t, n in c.most_common(8) if n >= 2][:5], hours))
    # 태그가 3개 이상 나오는 가장 짧은 창, 없으면 하나라도 나오는 가장 짧은 창
    top, hours = next((f for f in found if len(f[0]) >= 3), next((f for f in found if f[0]), ([], 24)))
    return [{"label": t if t.startswith("#") else f"#{t}", "q": t, "n": n} for t, n in top], hours


def overnight_summary(items: list[NewsItem], now: datetime, tickers: dict) -> dict:
    """최근 24시간 요약. 버킷은 서로 배타적이며 합계는 total과 같다(가격 급변은 조사일 기준으로 별도 표기)."""
    since = now - timedelta(hours=24)
    last24 = [it for it in items if it.event_time >= since]
    b = {"weather": 0, "pest": 0, "notice": 0, "release": 0, "price": 0, "other": 0}
    for it in last24:
        if it.category == "weather":
            b["weather"] += 1
        elif it.category == "pest":
            b["pest"] += 1
        elif it.category in ("subsidy_notice", "legislation"):
            b["notice"] += 1
        elif it.category in ("org_release", "stats", "tech_research"):
            b["release"] += 1
        elif it.category == "price":
            b["price"] += 1
        else:
            b["other"] += 1
    b["total"] = len(last24)
    b["surge"] = tickers.get("surge_count", 0)
    b["surge_day"] = tickers.get("day")
    return b
