"""정적 사이트 빌드: site/*.html(목록 7쪽 + 항목별 상세), feed.xml, brief.md, data/*.json"""
from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta
from itertools import groupby
from pathlib import Path

from markupsafe import Markup
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .model import NewsItem
from .util import hangul_only
from .rules import top5, tag_trend, overnight_summary, warning_kinds

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = ROOT / "data"
WEEKDAY = "월화수목금토일"
NAV = [("index.html", "홈"), ("news.html", "속보"), ("releases.html", "기관발표"), ("market.html", "시세"), ("stocks.html", "국제·증시"),
       ("alerts.html", "발령"), ("brief.html", "브리핑"), ("about.html", "정보")]
CATEGORIES = [("all", "전체"), ("weather", "특보"), ("price", "가격"), ("legislation", "정책·법령"),
              ("subsidy_notice", "공고"), ("org_release", "보도·설명"), ("pest", "병해충"), ("tech_research", "기술")]
TAB_OF = {"stats": "org_release"}          # 탭이 따로 없는 카테고리가 속할 탭
RELEASE_CATS = ("org_release", "stats", "legislation", "subsidy_notice", "tech_research")
LICENSE_LABEL = {"kogl1": "공공누리 1유형", "kogl2": "공공누리 2유형", "kogl3": "공공누리 3유형",
                 "kogl4": "공공누리 4유형", "no_limit": "제한 없음", "quote": "시세 인용", "unknown": "확인 중"}
TICKER_DEFAULT = {"day": None, "fixed": [], "surges": [], "surge_count": 0, "threshold": 10, "top10": [],
                  "top10_retail": [], "top10_whole": [], "rows_count": 0, "default_idx": 0, "all": []}


def f_spark(vals, w: int = 84, h: int = 26) -> str:
    """종가 목록 → SVG polyline 좌표."""
    v = [x for x in (vals or []) if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    span = (hi - lo) or 1
    return " ".join(f"{i * (w - 2) / (len(v) - 1) + 1:.1f},{h - 2 - (x - lo) / span * (h - 4):.1f}" for i, x in enumerate(v))


def f_sparkc(vals) -> str:
    """10일 흐름 색: 처음보다 오르면 빨강, 내리면 파랑."""
    v = [x for x in (vals or []) if x is not None]
    d = (v[-1] - v[0]) if len(v) > 1 else 0
    return "#d23a32" if d > 0 else "#2458d6" if d < 0 else "#8a90a0"


def f_num(v) -> str:
    return "–" if v is None else f"{v:,.0f}"


def f_px(r) -> str:
    """원화는 정수, 달러는 소수 둘째 자리."""
    v = r.get("price")
    if v is None:
        return "–"
    return f"{v:,.0f}" if r.get("cur") in ("KRW", "JPY") else f"{v:,.2f}"


def f_chg(r) -> str:
    v = r.get("chg") or 0
    arrow = "▲" if v > 0 else "▼" if v < 0 else ""
    return arrow + (f"{abs(v):,.0f}" if r.get("cur") in ("KRW", "JPY") else f"{abs(v):,.2f}")


def f_eok(v) -> str:
    """원 → 억 원 단위(1조 이상은 조)."""
    if not v:
        return "–"
    eok = v / 1e8
    return f"{eok / 1e4:,.1f}조" if eok >= 1e4 else f"{eok:,.0f}억"


def f_pct(v) -> str:
    if v is None:
        return "–"
    v = round(float(v), 1) + 0.0
    return f"{v:+.1f}%" if v else "0.0%"


def f_won(v) -> str:
    return f"{v:,.0f}" if v is not None else "–"


def f_man(v) -> str:
    """큰 통계값은 만 단위: 974,000 → '97.4만'."""
    if v is None:
        return "–"
    return f"{v / 1e4:,.1f}만" if abs(v) >= 1e5 else f"{v:,.0f}"


def f_time(it: NewsItem) -> str:
    return it.event_time.strftime("%m.%d %H:%M") if it.time_precision == "minute" else it.event_time.strftime("%m.%d")


def f_dt(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%m.%d %H:%M") if iso else "–"


def f_hp(url) -> str:
    """http(s) 주소에서 스킴을 뗀 host+path. 다른 스킴이면 빈 문자열(템플릿은 앞에 https:// 를 고정으로 붙인다)."""
    m = re.match(r"https?://(.+)$", str(url or "").strip(), re.I)
    return m.group(1) if m else ""


def cat_tab(it: NewsItem) -> str:
    return TAB_OF.get(it.category, it.category)


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "xml"]))
    env.filters.update(mini=f_mini, sparkc=f_sparkc, px=f_px, chg=f_chg, spark=f_spark, num=f_num, eok=f_eok, pct=f_pct, won=f_won, man=f_man, ftime=f_time, fdt=f_dt, tab=cat_tab, hp=f_hp)
    env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "separators": (",", ":")}
    return env


def _current(docs: list[NewsItem], now: datetime) -> NewsItem | None:
    """적용 기간이 오늘을 포함하는 호, 없으면 가장 최근 호."""
    for it in docs:
        start = it.extra.get("start")
        if start and it.expires_at and datetime.fromisoformat(start) <= now <= it.expires_at:
            return it
    return docs[0] if docs else None


def _related(it: NewsItem, items: list[NewsItem], n: int = 5) -> list[NewsItem]:
    tags = set(it.tags_items) | set(it.tags_events) | set(it.tags_regions)
    same_tag = [o for o in items if o.id != it.id and tags & (set(o.tags_items) | set(o.tags_events) | set(o.tags_regions))]
    same_src = [o for o in items if o.id != it.id and o.source_id == it.source_id and o not in same_tag]
    return (same_tag + same_src)[:n]


def f_mini(vals, uid: str, col: str, labels=None, unit: str = "", w: int = 240, h: int = 64) -> str:
    """KPI 카드용 작은 빗금 막대(마지막 막대만 진하게). 가격 비교 차트와 같은 모양."""
    pairs = [(x, (labels or [None] * len(vals))[i]) for i, x in enumerate(vals or []) if x]
    v = [x for x, _ in pairs]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    span = (hi - lo) or hi * 0.05
    lo, hi = max(0, lo - span * 1.2), hi + span * 0.1
    n, gap = len(v), 6
    bw = (w - gap * (n - 1)) / n
    out = [f'<svg class="msv" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true"><defs>'
           f'<pattern id="mh{uid}" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           f'<rect width="5" height="5" fill="{col}" fill-opacity=".07"/><line x1="0" y1="0" x2="0" y2="5" stroke="{col}" stroke-opacity=".25" stroke-width="1.2"/></pattern>'
           f'<linearGradient id="mg{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{col}"/><stop offset="1" stop-color="{col}" stop-opacity=".2"/></linearGradient></defs>']
    for i, x in enumerate(v):
        bh = max(3, (x - lo) / (hi - lo) * h)
        fill = f"url(#mg{uid})" if i == n - 1 else f"url(#mh{uid})"
        lab = pairs[i][1]
        num = f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}"
        tip = f"{lab} · {num}{unit}" if lab else f"{num}{unit}"
        out.append(f'<rect class="b" data-t="{tip}" x="{i * (bw + gap):.1f}" y="{h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{fill}"/>')
    return Markup("".join(out) + "</svg>")


CUR_UNIT = {"KRW": "원", "USD": "달러", "JPY": "엔"}


def _numtxt(v):
    """'1,189억' '9.17배' '2.12%' 같은 표시값에서 숫자만."""
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def enrich_market(stocks: dict, now: datetime) -> dict:
    """목록 시세에 상세 캐시(data/market/*.json)를 붙이고, 상세 페이지용 파생값을 계산한다."""
    from .collectors.naver_market import safe
    det_dir = DATA / "market"
    for sec, rows in (stocks.get("sections") or {}).items():
        for r in rows:
            r["sec"] = sec
            r["slug"] = "s-" + safe(r["code"]) + ".html"
            p = det_dir / f"{safe(r['code'])}.json"
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            if d.get("cur"):
                r["cur"] = d["cur"]
            info = {i["k"]: i["v"] for i in d.get("infos") or []}
            cut = (now - timedelta(days=366)).date().isoformat()
            hist = [h for h in d.get("hist") or [] if h["d"] >= cut and h.get("c")]
            closes = [h["c"] for h in hist]
            hi = _numtxt(info.get("52주 최고")) or (max(closes) if closes else None)
            lo = _numtxt(info.get("52주 최저")) or (min(closes) if closes else None)
            def ago(days):
                c = (now - timedelta(days=days)).date().isoformat()
                old = [h for h in hist if h["d"] <= c]
                return round((r["price"] - old[-1]["c"]) / old[-1]["c"] * 100, 2) if old and old[-1]["c"] else None
            r.update(info=info, hist=hist, hi52=hi, lo52=lo,
                     pos52=(round((r["price"] - lo) / (hi - lo) * 100) if hi and lo and hi > lo else None),
                     cap=info.get("시총") or ((d.get("etf") or {}).get("marketValue")), per=info.get("PER"),
                     vol_txt=info.get("거래량"), r1m=ago(30), r3m=ago(91), r1y=ago(365),
                     market=d.get("market"), month=d.get("month"), url=d.get("url"), trend=d.get("trend") or [],
                     news=d.get("news") or [], summary=d.get("summary"), target=d.get("target"),
                     unit=r.get("unit") or d.get("unit") or CUR_UNIT.get(r.get("cur"), ""))
    return stocks


PSTAT_CATS = ["식량작물", "채소류", "특용작물", "과일류", "축산물"]


def price_stats(tickers: dict) -> dict:
    """KAMIS 소매가 조사값으로 1년 전·1개월 전 대비 통계(부류별 품목 평균, 오른·내린 품목 수, 많이 움직인 품목)."""
    rows = [r for r in tickers.get("all") or [] if r.get("cls") == "소매" and r.get("year_ago")]
    if not rows:
        return {}
    for r in rows:
        r["yoy"] = round((r["price"] - r["year_ago"]) / r["year_ago"] * 100, 1) + 0.0
    cats = []
    for c in PSTAT_CATS + sorted({r["category"] for r in rows} - set(PSTAT_CATS)):
        g = [r["yoy"] for r in rows if r["category"] == c]
        if g:
            cats.append({"name": c, "n": len(g), "pct": round(sum(g) / len(g), 1) + 0.0})
    best: dict[str, dict] = {}                 # 같은 품목은 변동이 가장 큰 규격 1건
    for r in rows:
        if r["base"] not in best or abs(r["yoy"]) > abs(best[r["base"]]["yoy"]):
            best[r["base"]] = r
    one = sorted(best.values(), key=lambda r: -r["yoy"])
    return {"day": tickers.get("day"), "n": len(rows), "cats": cats,
            "up": sum(r["yoy"] > 0 for r in rows), "down": sum(r["yoy"] < 0 for r in rows),
            "risers": [r for r in one if r["yoy"] > 0][:5], "fallers": [r for r in reversed(one) if r["yoy"] < 0][:5]}


def build_site(cfg: dict, records: list[dict], status: dict, now: datetime, warn: dict | None = None) -> None:
    known_ids = {s["id"] for s in cfg["sources"]}
    items = [NewsItem.from_dict(d) for d in records if d.get("source_id") in known_ids]   # 설정에서 빠진 신호원의 옛 항목은 제외
    if warn and warn.get("stale"):       # 현황을 갱신하지 못했으면 옛 발효 목록을 현재 상태처럼 보여 주지 않는다
        warn = None
    # 같은 날 안에서는 분 단위 시각이 있는 항목을 먼저, 날짜만 있는 문서를 뒤에
    items.sort(key=lambda it: (it.event_time.date(), it.time_precision == "minute", it.event_time), reverse=True)

    tickers = dict(TICKER_DEFAULT)
    tp = DATA / "tickers.json"
    if tp.exists():
        tickers.update(json.loads(tp.read_text(encoding="utf-8")))
    tickers["stale"] = not status.get("kamis_daily", {"ok": True}).get("ok", True)
    stocks = {}
    spth = DATA / "stocks.json"
    if spth.exists():
        stocks = json.loads(spth.read_text(encoding="utf-8"))
    if stocks:
        stocks = enrich_market(stocks, now)
        st_ok = status.get("naver_market", {"ok": True})
        stocks["stale"] = not st_ok.get("ok", True)
        sec = stocks.get("sections", {})
        stocks["movers"] = sorted(sec.get("kr_stocks", []) + sec.get("world_stocks", []), key=lambda r: -abs(r["pct"]))
    pstat = price_stats(tickers)
    stp = DATA / "stats.json"
    kstat = json.loads(stp.read_text(encoding="utf-8")).get("series", {}) if stp.exists() else {}
    nav = [n for n in NAV if n[0] != "stocks.html" or stocks]   # 시세를 한 번도 받지 못했으면 메뉴에서 뺀다

    active = warn["active"] if warn else []
    active_kinds = None if warn is None else set().union(*[warning_kinds(a["kind"]) for a in active]) if active else set()
    t5 = top5(items, now, active_kinds)
    trend, trend_hours = tag_trend(items, now)
    overnight = overnight_summary(items, now, tickers)

    days = []
    for day, grp in groupby(items, key=lambda it: it.event_time.date()):
        days.append({"date": day.isoformat(), "label": f"{day.month}월 {day.day}일 ({WEEKDAY[day.weekday()]})",
                     "items": list(grp)})
    home_days, left = [], 20
    for d in days:
        if left <= 0:
            break
        home_days.append({**d, "items": d["items"][:left]})
        left -= len(d["items"])

    pests = [it for it in items if it.category == "pest"]
    weeklies = [it for it in items if it.source_id == "nongsaro_weekly"]
    latest_by_board = []
    for it in items:
        if it.category in RELEASE_CATS and it.source_id not in {x.source_id for x in latest_by_board}:
            latest_by_board.append(it)
    tab_counts = Counter(cat_tab(it) for it in items)
    tab_counts["all"] = len(items)
    failed = [s["name"] for s in status.values() if not s.get("ok") and not s.get("skipped")]
    # 키가 없어 한 번도 받지 못한 신호원은 화면(출처 표·기관 목록)에 올리지 않는다
    shown = [s for s in cfg["sources"] if not (status.get(s["id"], {}).get("skipped") and not status.get(s["id"], {}).get("last_ok"))]
    orgs = list(dict.fromkeys(s["org"] for s in shown))
    base_url = (os.environ.get("SITE_BASE_URL") or os.environ.get("CI_PAGES_URL") or cfg["site"].get("base_url") or "").rstrip("/")

    env = make_env()
    ctx = dict(
        site=cfg["site"], now=now, built=now.strftime("%Y.%m.%d %H:%M"), built_iso=now.isoformat(),
        nav=nav, stocks=stocks, categories=CATEGORIES, tab_counts=tab_counts, license_label=LICENSE_LABEL,
        items=items, days=days, home_days=home_days, top5=t5, trend=trend, trend_hours=trend_hours,
        overnight=overnight, tickers=tickers, warn=warn, active=active,
        pest=_current(pests, now), weekly=_current(weeklies, now), pests=pests,
        latest_by_board=latest_by_board, status=status, failed=failed, orgs=orgs,
        sources_cfg={s["id"]: s for s in cfg["sources"]}, shown_ids={s["id"] for s in shown}, base_url=base_url,
        release_cats=RELEASE_CATS, pstat=pstat, kstat=kstat,
        top5_js=[{"h": it.headline, "u": f"n-{it.id}.html"} for it in t5],
    )
    SITE.mkdir(exist_ok=True)
    brief_md = env.get_template("brief.md").render(**ctx)
    brief_md = hangul_only(brief_md)
    (SITE / "brief.md").write_text(brief_md, encoding="utf-8")
    (SITE / "brief.txt").write_text(brief_md, encoding="utf-8")
    ctx["brief_text"] = brief_md
    (SITE / "feed.xml").write_text(hangul_only(env.get_template("feed.xml").render(**ctx)), encoding="utf-8")
    for page, _ in NAV:
        (SITE / page).write_text(hangul_only(env.get_template(page).render(page=page, **ctx)), encoding="utf-8")
    (SITE / "404.html").write_text(hangul_only(env.get_template("404.html").render(page="404.html", **ctx)), encoding="utf-8")
    for old in list(SITE.glob("n-*.html")) + list(SITE.glob("s-*.html")):
        old.unlink()
    if stocks:
        stpl = env.get_template("stock_item.html")
        for sec, rows in stocks.get("sections", {}).items():
            for r in rows:
                peers = [o for o in rows if o["group"] == r["group"] and o["code"] != r["code"]]
                (SITE / r["slug"]).write_text(hangul_only(stpl.render(page="stocks.html", r=r, peers=peers, **ctx)), encoding="utf-8")
    tpl = env.get_template("item.html")
    for it in items:
        (SITE / f"n-{it.id}.html").write_text(
            hangul_only(tpl.render(page="news.html", it=it, related=_related(it, items), **ctx)), encoding="utf-8")
    st = ROOT / "static"                  # 로고·아이콘 같은 고정 파일
    if st.exists():
        shutil.copytree(st, SITE / "static", dirs_exist_ok=True)
    (SITE / "data").mkdir(exist_ok=True)
    for name in ("items.json", "status.json", "tickers.json", "stocks.json", "stats.json"):
        p = DATA / name
        if p.exists():
            shutil.copy(p, SITE / "data" / name)
    print(f"built {len(NAV)} pages + {len(items)} item pages")
