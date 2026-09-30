"""KAMIS 일별 가격(dailySalesList) 어댑터.
당일·전일·1개월 전·1년 전 조사값으로 시세바·시세표와 정기·급변 템플릿 속보를 만든다."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

import requests

from ..model import NewsItem
from ..util import now_kst, KST

RETAIL_URL = "https://www.kamis.or.kr/customer/price/retail/period.do"
WHOLE_URL = "https://www.kamis.or.kr/customer/price/wholesale/period.do"


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _disp(name: str) -> str:
    """'가지/가지' → '가지', '쌀/20kg' → '쌀 20kg'."""
    a, _, b = name.partition("/")
    return a if (not b or a == b) else b if a in b else f"{a} {b}"


def fetch_rows(src: dict) -> tuple[list[dict], str]:
    key = os.environ.get(src.get("env_key", "KAMIS_KEY")) or "111"
    cid = os.environ.get(src.get("env_id", "KAMIS_ID")) or "222"
    r = requests.get(src["url"], params={"p_cert_key": key, "p_cert_id": cid}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("error_code") != "000":
        raise RuntimeError(f"KAMIS error_code={j.get('error_code')}")
    skip = set(src.get("exclude_categories") or [])
    rows = []
    for p in j.get("price", []):
        if p.get("category_name") in skip:
            continue
        cur, prev = _num(p.get("dpr1")), _num(p.get("dpr2"))
        if cur is None or prev in (None, 0):
            continue  # 결측('-')·규격 변경 품목은 계산에서 제외
        name = p.get("productName") or p.get("item_name") or ""
        rows.append({
            "no": p.get("productno"), "name": name, "disp": _disp(name), "base": name.split("/")[0],
            "cls": p.get("product_cls_name"),          # 소매/도매
            "category": p.get("category_name"), "unit": p.get("unit"),
            "price": cur, "prev": prev, "month_ago": _num(p.get("dpr3")), "year_ago": _num(p.get("dpr4")),
            "pct": round((cur - prev) / prev * 100, 1) + 0.0,   # +0.0: -0.0 방지
            "day": p.get("lastest_day"),
        })
    day = (j.get("condition") or [[""]])[0][0]
    if not rows or not day:
        raise RuntimeError("KAMIS empty response")
    return rows, day


def collect(src: dict, threshold_pct: float = 10.0) -> tuple[list[NewsItem], dict]:
    rows, day = fetch_rows(src)
    fetched = now_kst()
    survey_day = datetime.strptime(day, "%Y%m%d").replace(tzinfo=KST) if day else fetched
    rows.sort(key=lambda r: (r["category"] or "", r["cls"] or "", r["name"]))
    for i, r in enumerate(rows):
        r["idx"] = i
        # 날이 바뀌어 행 순서가 달라져도 같은 품목을 가리키는 키(시세 링크용)
        r["key"] = hashlib.sha256(f"{r['name']}|{r['cls']}|{r['unit']}".encode()).hexdigest()[:8]

    def pick(prefix: str, cls: str = "소매"):
        return next((r for r in rows if r["cls"] == cls and r["name"].startswith(prefix)), None)

    fixed = [r for r in (pick(p) for p in src.get("ticker_items", ["쌀/20kg", "배추"])) if r]
    # 급변: 같은 품목·구분에서는 변동이 가장 큰 규격 1건만
    best: dict[tuple, dict] = {}
    for r in rows:
        if abs(r["pct"]) >= threshold_pct:
            k = (r["base"], r["cls"])
            if k not in best or abs(r["pct"]) > abs(best[k]["pct"]):
                best[k] = r
    surges = sorted(best.values(), key=lambda r: -abs(r["pct"]))
    by_abs = sorted(rows, key=lambda r: -abs(r["pct"]))
    rice, cabbage = pick("쌀/20kg"), pick("배추")

    items: list[NewsItem] = []
    parts = []
    if rice:
        parts.append(f"쌀 20kg 소매 평균가는 {rice['price']:,.0f}원으로 전일 대비 {rice['pct']:+.1f}%다.")
    if cabbage:
        parts.append(f"배추({cabbage['unit']}) 소매가는 {cabbage['price']:,.0f}원({cabbage['pct']:+.1f}%)이다.")
    parts.append(f"전일 대비 {threshold_pct:.0f}% 이상 움직인 품목은 {len(surges)}개다.")
    items.append(NewsItem(
        source_id=src["id"], source_name=src["name"], org=src["org"], url=RETAIL_URL,
        headline=f"KAMIS {survey_day.month}월 {survey_day.day}일 조사, 쌀 20kg {rice['price']:,.0f}원" if rice
        else f"KAMIS {survey_day.month}월 {survey_day.day}일 조사 {len(rows)}개 품목·규격",
        event_time=survey_day, time_precision="date", fetched_at=fetched,
        category="price", label="정기", license=src.get("license", "unknown"),
        body=f"aT KAMIS에 따르면 {survey_day.month}월 {survey_day.day}일 조사 기준 " + " ".join(parts),
        body_mode="template", generator="template", tags_items=[t for t, r in (("쌀", rice), ("배추", cabbage)) if r],
        uid=f"daily|{day}", time_source="survey", extra={"market_key": rice["key"]} if rice else {},
    ))
    for r in surges[:5]:
        rday = datetime.strptime(r["day"], "%Y-%m-%d").replace(tzinfo=KST) if r.get("day") else survey_day
        items.append(NewsItem(
            source_id=src["id"], source_name=src["name"], org=src["org"],
            url=WHOLE_URL if r["cls"] == "도매" else RETAIL_URL,
            headline=f"{r['disp']} {r['cls']} {r['price']:,.0f}원/{r['unit']}, 전일比 {r['pct']:+.1f}%",
            event_time=rday, time_precision="date", fetched_at=fetched,
            category="price", label="급변", license=src.get("license", "unknown"), severity="advisory",
            body=(f"aT KAMIS {rday.month}월 {rday.day}일 조사에 따르면 {r['disp']}({r['unit']}) {r['cls']} 평균가는 "
                  f"{r['price']:,.0f}원으로 전일({r['prev']:,.0f}원) 대비 {r['pct']:+.1f}%다."
                  + (f" 1개월 전은 {r['month_ago']:,.0f}원, 1년 전은 {r['year_ago']:,.0f}원이다." if r['month_ago'] and r['year_ago'] else "")),
            body_mode="template", generator="template", tags_items=[r["base"]],
            uid=f"surge|{r['day']}|{r['name']}|{r['cls']}", time_source="survey", extra={"market_key": r["key"]},
        ))
    tickers = {
        "day": survey_day.date().isoformat(),
        "fixed": fixed,
        "surges": surges,
        "surge_count": len(surges),
        "threshold": threshold_pct,
        "top10": by_abs[:10],
        "top10_retail": [r for r in by_abs if r["cls"] == "소매"][:10],
        "top10_whole": [r for r in by_abs if r["cls"] == "도매"][:10],
        "rows_count": len(rows),
        "default_idx": rice["idx"] if rice else (by_abs[0]["idx"] if by_abs else 0),
        "all": rows,
    }
    return items, tickers
