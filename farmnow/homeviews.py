"""홈 화면 시안용 자료: 시세 행에 아이콘·기간별 등락을 붙이고, 순위·등락 지도·오늘 요약을 만든다."""
from __future__ import annotations

from datetime import datetime

EMO = {"쌀": "🌾", "찹쌀": "🌾", "현미": "🌾", "보리쌀": "🌾", "혼식곡": "🌾", "콩": "🫘", "팥": "🫘", "녹두": "🫘", "감자": "🥔", "고구마": "🍠",
       "배추": "🥬", "알배기배추": "🥬", "얼갈이배추": "🥬", "양배추": "🥬", "상추": "🥬", "시금치": "🥬", "열무": "🥬", "깻잎": "🌿", "미나리": "🌿", "부추": "🌿",
       "무": "🥕", "당근": "🥕", "양파": "🧅", "파": "🧅", "깐마늘(국산)": "🧄", "생강": "🫚", "건고추": "🌶️", "고춧가루": "🌶️", "붉은고추": "🌶️", "풋고추": "🌶️",
       "피망": "🫑", "파프리카": "🫑", "오이": "🥒", "호박": "🥒", "가지": "🍆", "토마토": "🍅", "방울토마토": "🍅", "브로콜리": "🥦", "수박": "🍉", "멜론": "🍈",
       "사과": "🍎", "배": "🍐", "감귤": "🍊", "레몬": "🍋", "바나나": "🍌", "포도": "🍇", "키위": "🥝", "망고": "🥭", "파인애플": "🍍", "단감": "🟠", "블루베리": "🫐",
       "아보카도": "🥑", "소": "🥩", "수입 소고기": "🥩", "돼지": "🐖", "수입 돼지고기": "🐖", "닭": "🐔", "계란": "🥚", "우유": "🥛", "참깨": "🌰", "땅콩": "🥜",
       "느타리버섯": "🍄", "팽이버섯": "🍄", "새송이버섯": "🍄"}
# 등락 지도 칸 크기(장바구니 비중을 어림한 값, 나머지는 1)
WEIGHT = {"쌀": 6, "돼지": 5, "소": 5, "배추": 4, "사과": 4, "계란": 4, "닭": 3, "양파": 3, "무": 3, "감자": 3, "깐마늘(국산)": 3, "건고추": 3, "우유": 3, "배": 3, "감귤": 3}
WATCH = ("쌀", "배추", "사과", "양파", "돼지", "계란")
ORG_CATS = ("org_release", "legislation", "subsidy_notice", "tech_research")


def _chg(a, b):
    return (a - b) / b * 100 if a is not None and b else None


def enrich(r: dict) -> dict:
    r = dict(r, e=EMO.get(r.get("base"), "•"))
    r["m"] = _chg(r["price"], r.get("month_ago"))
    r["y"] = _chg(r["price"], r.get("year_ago"))
    r["n"] = None
    s = r.get("season")
    if s:
        lm = max((i for i, v in enumerate(s["cur"]) if v is not None), default=None)
        if lm is not None and s["avg"][lm]:
            r["n"] = (s["cur"][lm] / s["avg"][lm] - 1) * 100
    r["four"] = [r.get("year_ago"), r.get("month_ago"), r.get("prev"), r["price"]]
    return r


def squarify(items, x, y, w, h):
    """[(무게, 값)] → [(값, x, y, w, h)] 칸 비율이 정사각형에 가깝게 나눈다."""
    out, items = [], sorted(items, key=lambda a: -a[0])
    tot = sum(a[0] for a in items) or 1
    items = [(a[0] * w * h / tot, a[1]) for a in items]

    def worst(row, side):
        s = sum(a[0] for a in row)
        return max(max(side * side * a[0] / (s * s), (s * s) / (side * side * a[0])) for a in row)

    while items:
        side = min(w, h)
        row = [items.pop(0)]
        while items and worst(row + [items[0]], side) <= worst(row, side):
            row.append(items.pop(0))
        s = sum(a[0] for a in row)
        if w >= h:
            cw, yy = s / h, y
            for a in row:
                out.append((a[1], x, yy, cw, a[0] / cw))
                yy += a[0] / cw
            x, w = x + cw, w - cw
        else:
            rh, xx = s / w, x
            for a in row:
                out.append((a[1], xx, y, a[0] / rh, rh))
                xx += a[0] / rh
            y, h = y + rh, h - rh
    return out


def treemap(rows, W=1140, H=560):
    seen, uniq = set(), []
    for r in rows:
        if r["base"] not in seen:
            seen.add(r["base"])
            uniq.append(r)
    cats: dict[str, list] = {}
    for r in uniq:
        cats.setdefault(r["category"], []).append(r)
    groups = []
    for c, cx, cy, cw, ch in squarify([(sum(WEIGHT.get(r["base"], 1) for r in rs), c) for c, rs in cats.items()], 0, 0, W, H):
        tiles = [{"r": r, "x": x, "y": y, "w": w, "h": h}
                 for r, x, y, w, h in squarify([(WEIGHT.get(r["base"], 1), r) for r in cats[c]], cx + 2, cy + 18, cw - 4, ch - 20)]
        groups.append({"name": c, "x": cx, "y": cy, "w": cw, "h": ch, "tiles": tiles})
    return {"W": W, "H": H, "groups": groups, "n": len(uniq)}


def _pct(v, d=1):
    return "–" if v is None else f"{v:+.{d}f}%"


def summary(rows, fixed, items, notices, active):
    """규칙으로 쓰는 '오늘 시장 요약' 몇 줄(앞·뒤 글자, 굵게 쓸 말)."""
    out = []
    rice = next((enrich(r) for r in fixed if r.get("base") == "쌀"), None)
    if rice:
        out.append(("쌀 20kg은 ", f"{rice['price']:,.0f}원", f"으로 전일보다 {_pct(rice['pct'])}, 1년 전보다 {_pct(rice['y'])}입니다."))
    up = sorted([r for r in rows if r["pct"] > 0], key=lambda r: -r["pct"])
    dn = sorted([r for r in rows if r["pct"] < 0], key=lambda r: r["pct"])
    if up and dn:
        out.append(("가장 많이 오른 품목은 ", f"{up[0]['disp']}({_pct(up[0]['pct'])})", f", 가장 많이 내린 품목은 {dn[0]['disp']}({_pct(dn[0]['pct'])})입니다."))
    hot = sorted([r for r in rows if (r["n"] or 0) >= 15], key=lambda r: -r["n"])[:4]
    cold = sorted([r for r in rows if r["n"] is not None and r["n"] <= -15], key=lambda r: r["n"])[:4]
    if hot:
        out.append(("평년보다 15% 이상 비싼 품목: ", ", ".join(f"{r['disp']} {_pct(r['n'], 0)}" for r in hot), ""))
    if cold:
        out.append(("평년보다 15% 이상 싼 품목: ", ", ".join(f"{r['disp']} {_pct(r['n'], 0)}" for r in cold), ""))
    if items:
        last = items[0].event_time
        n24 = sum(1 for it in items if it.category in ORG_CATS and (last - it.event_time).total_seconds() < 86400)
        out.append(("최근 24시간 정부 발표 ", f"{n24}건", f", 진행 중 지원 공고 {len(notices)}건, 발효 중 기상특보 {len(active)}건."))
    return out


def build(tickers: dict, items: list, stocks: dict, warn: dict | None) -> dict:
    rows = [enrich(r) for r in tickers.get("all") or [] if r.get("cls") == "소매"]
    fixed = [enrich(r) for r in tickers.get("fixed") or []]
    news = sorted(items, key=lambda it: it.event_time, reverse=True)
    orgs = [it for it in news if it.category in ORG_CATS]
    notices = [it for it in news if it.category == "subsidy_notice"]
    active = (warn or {}).get("active") or []
    comm = (stocks.get("sections") or {}).get("commodities") or []
    fx = stocks.get("usdkrw")
    idx = [{"name": f"{r['e']} {r['disp']}", "val": f"{r['price']:,.0f}원", "pct": r["pct"], "spark": r["four"], "href": f"market?pick={r['key']}"} for r in fixed[:3]]
    if comm:
        idx.append({"name": "🌽 국제 옥수수", "val": f"{comm[0]['price']:,.2f}", "pct": comm[0].get("pct"), "spark": comm[0].get("spark") or [], "href": "stocks"})
    if fx:
        idx.append({"name": "💱 원/달러", "val": f"{fx['price']:,.1f}원", "pct": fx.get("pct"), "spark": fx.get("spark") or [], "href": "stocks"})
    watch = [r for r in (next((x for x in rows if x["base"] == b), None) for b in WATCH) if r]
    ranks = [("up", "많이 오른", sorted([r for r in rows if r["pct"] > 0], key=lambda r: -r["pct"]), "pct", "전일 대비"),
             ("dn", "많이 내린", sorted([r for r in rows if r["pct"] < 0], key=lambda r: r["pct"]), "pct", "전일 대비"),
             ("nm", "평년보다 비싼", sorted([r for r in rows if r["n"] is not None], key=lambda r: -r["n"]), "n", "평년 대비"),
             ("yr", "1년 전보다", sorted([r for r in rows if r["y"] is not None], key=lambda r: -abs(r["y"])), "y", "1년 전 대비")]
    cats = []
    for r in rows:
        if r["category"] not in cats:
            cats.append(r["category"])
    return {"rows": rows, "fixed": fixed, "news": news, "orgs": orgs, "notices": notices, "active": active, "comm": comm,
            "idx": idx, "watch": watch, "ranks": ranks, "tm": treemap(rows), "summary": summary(rows, tickers.get("fixed") or [], news, notices, active),
            "cats": cats, "day": tickers.get("day") or "", "now": datetime.now()}
