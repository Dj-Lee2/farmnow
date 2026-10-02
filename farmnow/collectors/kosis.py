"""국가데이터처 KOSIS 통계표 어댑터: 연간(Y)·분기(Q)·월간(M) 통계(농가·경지·생산량·농가경제·가격지수·가축·소비·생산비).
해마다 한 번 바뀌는 값이라 refresh_hours(기본 24시간)마다 한 번만 받고, 그 사이에는 data/stats.json을 그대로 쓴다.
통계표마다 분류 구성이 달라, 설정의 itm(항목 이름)·want(분류 이름)와 맞고 나머지 분류가 모두 '계·전국'인 행만 고른다."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from ..util import now_kst

URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
TOTALS = {"계", "합계", "총계", "소계", "전국", "전체", "전국계"}
CLS = [f"C{i}_NM" for i in range(1, 9)]


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_table(key: str, tbl: str, org: str = "101", years: int = 10, max_dims: int = 4, se: str = "Y") -> list[dict]:
    """분류 개수를 몰라도 되도록 objL1부터 하나씩 늘려 가며 'ALL'로 요청한다."""
    last = None
    for n in range(1, max_dims + 1):
        params = {"method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y", "orgId": org, "tblId": tbl,
                  "itmId": "ALL", "prdSe": se, "newEstPrdCnt": str(years)}
        params.update({f"objL{i}": "ALL" for i in range(1, n + 1)})
        r = requests.get(URL, params=params, timeout=40)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, list):
            return j
        last = j
        if str(j.get("err")) not in ("20", "21"):      # 20·21 = 분류 변수 누락·잘못 → 분류를 하나 더 붙여 다시
            break
    raise RuntimeError(f"KOSIS {tbl} err={last.get('err') if last else '?'} {(last or {}).get('errMsg', '')}"[:120])


def pick_series(rows: list[dict], itm: str, want: list[str] | None = None, se: str = "Y", total: bool = False,
                codes: dict | None = None) -> dict:
    """연도별 값 하나씩. 항목 이름은 itm과, want의 각 정규식은 항목·분류 이름 중 하나와 맞아야 하고
    want에 걸리지 않은 분류는 모두 '계·전국' 같은 합계여야 한다. 한 해에 여러 행이 맞으면 항목 이름이 가장 짧은 행,
    total이면 맞는 행들의 합(예: 65~69세+70~74세+…, 전국 행이 없는 표는 시도 행의 합).
    codes는 {'C1': 정규식}처럼 분류 코드로 거른다(이름이 모두 '소계'처럼 같아 이름으로 못 가르는 표)."""
    want_re = [re.compile(w) for w in (want or [])]
    itm_re = re.compile(itm)
    code_re = {c: re.compile(p) for c, p in (codes or {}).items()}
    best: dict[str, tuple[int, dict]] = {}
    for r in rows:
        name = (r.get("ITM_NM") or "").strip()
        if not itm_re.search(name):
            continue
        if any(not p.search(str(r.get(c) or "")) for c, p in code_re.items()):
            continue
        cls = [(r.get(c) or "").strip() for c in CLS if r.get(c)]
        labels = [name] + cls
        if not all(any(w.search(x) for x in labels) for w in want_re):
            continue
        if any(x not in TOTALS and not any(w.search(x) for w in want_re) for x in cls):
            continue
        v = _num(r.get("DT"))
        if v is None:
            continue
        prd = str(r.get("PRD_DE", ""))
        y = prd[:4] if se == "Y" else f"{prd[:4]}.{prd[4:6]}" if se == "M" else prd   # 월별은 '2026.07'로 분기와 구분
        if total:
            key = tuple(labels) + tuple(str(r.get(c)) for c in code_re)   # 같은 행이 두 번 오면 한 번만 더한다
            if y in best and key not in best[y][1]["seen"]:
                best[y][1]["v"] += v
                best[y][1]["seen"].add(key)
            elif y not in best:
                best[y] = (0, {"v": v, "unit": r.get("UNIT_NM") or "", "tbl_nm": r.get("TBL_NM") or "", "seen": {key}})
            continue
        if y not in best or len(name) < best[y][0]:
            best[y] = (len(name), {"v": v, "unit": r.get("UNIT_NM") or "", "tbl_nm": r.get("TBL_NM") or ""})
    years = sorted(best)
    if not years:
        return {}
    return {"years": years, "vals": [best[y][1]["v"] for y in years], "unit": best[years[-1]][1]["unit"],
            "tbl_nm": best[years[-1]][1]["tbl_nm"]}


def _labels_sample(rows: list[dict], n: int = 6) -> str:
    seen = []
    for r in rows:
        s = "/".join([r.get("ITM_NM") or ""] + [r.get(c) for c in CLS if r.get(c)])
        if s not in seen:
            seen.append(s)
        if len(seen) >= n:
            break
    return ", ".join(seen)


def collect(src: dict, data_dir: Path) -> tuple[list, dict]:
    path = data_dir / "stats.json"
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            prev = {}
    now = now_kst()
    ids = [s["id"] for s in src["series"]]
    fresh_until = timedelta(hours=src.get("refresh_hours", 24))
    if prev.get("fetched_at") and set(ids) <= set(prev.get("series", {})) \
            and now - datetime.fromisoformat(prev["fetched_at"]) < fresh_until:
        return [], prev
    key = os.environ.get(src.get("env_key", "KOSIS_API_KEY"))
    if not key:
        if prev.get("series"):
            return [], prev
        raise RuntimeError("no_api_key")

    tables: dict[str, list[dict]] = {}
    out, errors = {}, []
    for s in src["series"]:
        try:
            se = s.get("se", "Y")
            tk = (s["tbl"], se)
            if tk not in tables:
                tables[tk] = fetch_table(key, s["tbl"], s.get("org", "101"), s.get("periods", src.get("years", 10)), se=se)
            got = pick_series(tables[tk], s["itm"], s.get("want"), se, s.get("sum", False), s.get("codes"))
            if not got:
                raise RuntimeError(f"맞는 행 없음 ({_labels_sample(tables[tk])})")
            out[s["id"]] = {"title": s["title"], "tbl": s["tbl"], "org": str(s.get("org", "101")), "survey": s.get("survey", ""), "se": se,
                            **got, "unit": s.get("unit") or got["unit"]}
        except Exception as e:  # 한 계열이 실패해도 지난 값을 쓴다
            errors.append(f"{s['id']}: {type(e).__name__}: {e}"[:200])
            if s["id"] in prev.get("series", {}):
                out[s["id"]] = prev["series"][s["id"]]
    for e in errors:
        print(f"[warn] kosis {e}", file=sys.stderr)
    if not out:
        raise RuntimeError(errors[0] if errors else "KOSIS empty")
    stats = {"fetched_at": now.isoformat(), "series": out}
    if errors:          # 일부 실패면 다음 회차에 다시 시도하도록 이전 수집 시각을 남긴다
        stats["fetched_at"] = prev.get("fetched_at") or (now - fresh_until).isoformat()
    return [], stats
