"""KAMIS 월별 소매가격(monthlySalesList) → 품목별 평년 범위.
올해 월평균과 최근 5년 같은 달의 최저·최고·평균을 시세 행에 붙인다(시세 쪽 '평년 범위' 그래프용).
하루 한 번만 받아 data/season.json에 둔다."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests

from ..util import now_kst

URL = "https://www.kamis.or.kr/service/price/xml.do"
TTL_HOURS = 20
PAST_YEARS = 5


def _num(s) -> float | None:
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def band(rows: list[dict], year: int) -> dict | None:
    """연도별 월값 행 → {cur, lo, hi, avg}. 같은 달 과거 값이 3해 미만이면 그 달은 비운다."""
    by = {int(r["yyyy"]): [_num(r.get(f"m{m}")) for m in range(1, 13)] for r in rows if str(r.get("yyyy", "")).isdigit()}
    if year not in by:
        return None
    past = [by[y] for y in range(year - PAST_YEARS, year) if y in by]
    lo, hi, avg = [], [], []
    for m in range(12):
        vs = [p[m] for p in past if p[m] is not None]
        ok = len(vs) >= 3
        lo.append(min(vs) if ok else None)
        hi.append(max(vs) if ok else None)
        avg.append(round(sum(vs) / len(vs)) if ok else None)
    cur = by[year]
    if sum(v is not None for v in cur) < 2 or sum(v is not None for v in avg) < 6:
        return None
    return {"year": year, "cur": cur, "lo": lo, "hi": hi, "avg": avg}


def fetch_one(item: dict, year: int, key: str, cid: str) -> dict | None:
    params = {"action": "monthlySalesList", "p_yyyy": str(year), "p_period": str(PAST_YEARS),
              "p_itemcategorycode": item["cat"], "p_itemcode": item["item"], "p_kindcode": item.get("kind", ""),
              "p_graderank": "1", "p_countycode": "", "p_convert_kg_yn": "N",
              "p_cert_key": key, "p_cert_id": cid, "p_returntype": "json"}
    r = requests.get(URL, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    for p in j.get("price") or []:
        if not isinstance(p, dict) or p.get("productclscode") != "01":
            continue
        parts = (p.get("caption") or "").split(" > ")      # 소매가격 > 부류 > 품목 > 품종 > 등급 > 단위
        if len(parts) < 6:
            return None
        b = band(p.get("item") or [], year)
        if b:
            b.update(kind=parts[3], unit=parts[5])
        return b
    return None


def refresh(items: list[dict], data_dir: Path, src: dict, now: datetime | None = None) -> dict:
    """캐시가 TTL 안이면 그대로, 아니면 다시 받는다. 일부 실패는 이전 값을 유지한다."""
    now = now or now_kst()
    path = data_dir / "season.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    at = old.get("fetched_at")
    if at and (now - datetime.fromisoformat(at)).total_seconds() < TTL_HOURS * 3600 and old.get("items"):
        return old
    key = os.environ.get(src.get("env_key", "KAMIS_KEY")) or "111"
    cid = os.environ.get(src.get("env_id", "KAMIS_ID")) or "222"
    got = dict(old.get("items") or {})
    ok = 0
    for it in items:
        try:
            b = fetch_one(it, now.year, key, cid)
        except Exception:
            continue
        if b:
            got[it["name"]] = b
            ok += 1
    if not ok:
        raise RuntimeError("KAMIS 월별 가격을 하나도 받지 못함")
    out = {"fetched_at": now.isoformat(), "items": got}
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def attach(tickers: dict, season: dict) -> int:
    """시세 소매 행에 평년 범위를 붙인다. 단위가 다르거나 오늘 값이 올해 최근 월평균과 너무 다르면(품종 불일치) 뺀다."""
    n = 0
    items = season.get("items") or {}
    for r in tickers.get("all") or []:
        b = items.get(r.get("name"))
        if not b or r.get("cls") != "소매" or b.get("unit") != r.get("unit"):
            continue
        last = next((v for v in reversed(b["cur"]) if v is not None), None)
        if not last or not 0.5 <= r["price"] / last <= 2:
            continue
        r["season"] = b
        n += 1
    return n
