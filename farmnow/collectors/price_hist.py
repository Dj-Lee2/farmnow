"""공공데이터포털 aT '일별 도·소매 가격정보'(B552845/perDay)로 시세 행마다 날짜별 전국 평균을 쌓는다.
- 매 회차: 오늘 시세(recent)를 그날 값으로 넣는다(호출 없음).
- 뒤로 채우기: 회차당 호출 수·시간 한도 안에서, 우선순위 높은 품목부터 180일씩 과거로 내려가며 받는다.
  perDay는 시장(조사처)별 값이라 같은 날·같은 단위 값의 평균을 낸다 — recent의 조사일 평균가와 같다(11-19 배추 2,353원 확인).
- data/price_hist.json: {"series": {key: {"q": 조회 코드, "d": {"YYYYMMDD": 원}, "back": 받은 가장 오래된 날, "done": bool}}}
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

URL = "https://apis.data.go.kr/B552845/perDay/price"
YEARS = 6            # 평년(최근 5년 같은 달) + 올해
WINDOW_DAYS = 180


def _unit(i: dict) -> str:
    s = (i.get("unit_sz") or "").strip()
    s = s[:-2] if s.endswith(".0") else s
    return f"{s}{i.get('unit') or ''}"


def day_means(items: list[dict], unit: str) -> dict[str, int]:
    """시장별 행 → 날짜별 평균(단위가 같은 행만)."""
    acc: dict[str, list[float]] = defaultdict(list)
    for i in items:
        if _unit(i) != unit:
            continue
        try:
            acc[i["exmn_ymd"]].append(float(i["exmn_dd_prc"]))
        except (KeyError, TypeError, ValueError):
            continue
    return {d: round(sum(v) / len(v)) for d, v in acc.items() if v}


def fetch_range(q: dict, start: str, end: str, key: str, budget: list[int]) -> list[dict] | None:
    """[start, end] 기간을 페이지를 넘기며 받는다. 호출 한도를 넘거나 실패하면 None."""
    out, page = [], 1
    while True:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        params = {"serviceKey": key, "pageNo": page, "numOfRows": 1000, "returnType": "json",
                  "cond[exmn_ymd::GTE]": start, "cond[exmn_ymd::LTE]": end,
                  "cond[ctgry_cd::EQ]": q["ctgry"], "cond[item_cd::EQ]": q["item"], "cond[se_cd::EQ]": q["se"],
                  "cond[vrty_cd::EQ]": q["vrty"], "cond[grd_cd::EQ]": q["grd"]}
        r = requests.get(URL, params=params, timeout=40)
        r.raise_for_status()
        body = (r.json().get("response") or {}).get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        items = items if isinstance(items, list) else [items]
        out += items
        total = int(body.get("totalCount") or 0)
        if not items or page * 1000 >= total:
            return out
        page += 1


def update(rows: list[dict], path: Path, key: str | None, now: datetime,
           max_calls: int = 60, max_seconds: float = 120, priority: list[str] | None = None) -> dict:
    """시세 행(오늘 값·조회 코드 포함)으로 기록을 늘리고 정해진 한도 안에서 과거를 채운다."""
    stamp, now = now.isoformat(), now.replace(tzinfo=None)   # 날짜 계산은 KST 기준 벽시계로
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    S: dict = old.get("series") or {}
    for r in rows:
        q = r.get("q")
        if not q or r.get("price") is None:
            continue
        s = S.setdefault(r["key"], {"q": q, "d": {}, "back": None, "done": False})
        s.update(name=r["name"], cls=r["cls"], unit=r["unit"], q=q)
        day = (r.get("day") or "").replace("-", "")
        if day:
            s["d"][day] = round(r["price"])
    target = f"{now.year - YEARS + 1}0101"
    order = sorted((k for k, s in S.items() if not s.get("done")),
                   key=lambda k: (k not in (priority or []), (priority or []).index(k) if k in (priority or []) else 0,
                                  S[k].get("cls") != "소매"))
    budget, t0, filled = [max_calls], time.time(), 0
    if key:
        for k in order:
            s = S[k]
            while not s["done"] and budget[0] > 0 and time.time() - t0 < max_seconds:
                end = datetime.strptime(s["back"], "%Y%m%d") - timedelta(days=1) if s["back"] else now - timedelta(days=1)
                start = max(end - timedelta(days=WINDOW_DAYS), datetime.strptime(target, "%Y%m%d"))
                try:
                    got = fetch_range(s["q"], start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), key, budget)
                except (requests.RequestException, ValueError) as e:
                    print(f"[warn] price_hist {s.get('name')}: {type(e).__name__}: {e}"[:160], file=sys.stderr)
                    budget[0] = 0
                    break
                if got is None:
                    break
                for d, v in day_means(got, s["q"]["unit"]).items():
                    s["d"].setdefault(d, v)
                s["back"] = start.strftime("%Y%m%d")
                s["done"] = s["back"] <= target
                filled += 1
            if budget[0] <= 0 or time.time() - t0 >= max_seconds:
                break
    out = {"updated_at": stamp, "series": S,
           "progress": {"done": sum(1 for s in S.values() if s.get("done")), "total": len(S), "windows": filled,
                        "calls": max_calls - budget[0]}}
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out


def monthly_rows(d: dict[str, int]) -> list[dict]:
    """날짜별 값 → kamis_season.band가 받는 연도별 월평균 행."""
    acc: dict[tuple, list[int]] = defaultdict(list)
    for day, v in d.items():
        acc[(int(day[:4]), int(day[4:6]))].append(v)
    rows: dict[int, dict] = {}
    for (y, m), vs in acc.items():
        rows.setdefault(y, {"yyyy": str(y)})[f"m{m}"] = str(round(sum(vs) / len(vs)))
    return list(rows.values())


def weekly(d: dict[str, int], since: str) -> list[list]:
    """날짜별 값 → 주(월요일 시작)별 평균 [[YYYY-MM-DD, 원], ...] — 그래프 점 수를 줄인다."""
    acc: dict[str, list[int]] = defaultdict(list)
    for day, v in d.items():
        if day < since:
            continue
        dt = datetime.strptime(day, "%Y%m%d")
        acc[(dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")].append(v)
    return [[w, round(sum(vs) / len(vs))] for w, vs in sorted(acc.items())]


def attach(tickers: dict, hist: dict, now: datetime) -> list[str]:
    """시세 행에 평년 범위(season)를 붙이고, 1년 이상 기록이 있는 행의 키 목록을 돌려준다(가격 흐름 그래프용).
    기록으로 평년을 못 만드는 행은 예전 KAMIS 월별 캐시(season.json)를 그대로 쓴다."""
    from . import kamis_season
    S = hist.get("series") or {}
    long_keys = []
    for r in tickers.get("all") or []:
        s = S.get(r.get("key"))
        if not s or not s.get("d"):
            continue
        days = sorted(s["d"])
        if days[0] <= (now - timedelta(days=365)).strftime("%Y%m%d"):
            long_keys.append(r["key"])
        if r.get("cls") == "소매":
            b = kamis_season.band(monthly_rows(s["d"]), now.year)
            if b:
                base, _, vrty = r["name"].partition("/")
                b.update(kind=vrty if vrty and vrty not in (r["unit"], base) else base, unit=r["unit"], via="data.go.kr")
                r["season"] = b
    return long_keys
