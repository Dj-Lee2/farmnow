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

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .model import NewsItem
from .rules import top5, tag_trend, overnight_summary, warning_kinds

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = ROOT / "data"
WEEKDAY = "월화수목금토일"
NAV = [("index.html", "홈"), ("news.html", "속보"), ("releases.html", "기관발표"), ("market.html", "시세"),
       ("alerts.html", "발령"), ("brief.html", "브리핑"), ("about.html", "정보")]
CATEGORIES = [("all", "전체"), ("weather", "특보"), ("price", "가격"), ("legislation", "정책·법령"),
              ("subsidy_notice", "공고"), ("org_release", "보도·설명"), ("pest", "병해충"), ("tech_research", "기술")]
TAB_OF = {"stats": "org_release"}          # 탭이 따로 없는 카테고리가 속할 탭
RELEASE_CATS = ("org_release", "stats", "legislation", "subsidy_notice", "tech_research")
LICENSE_LABEL = {"kogl1": "공공누리 1유형", "kogl2": "공공누리 2유형", "kogl3": "공공누리 3유형",
                 "kogl4": "공공누리 4유형", "no_limit": "제한 없음", "unknown": "확인 중"}
TICKER_DEFAULT = {"day": None, "fixed": [], "surges": [], "surge_count": 0, "threshold": 10, "top10": [],
                  "top10_retail": [], "top10_whole": [], "rows_count": 0, "default_idx": 0, "all": []}


def f_pct(v) -> str:
    if v is None:
        return "–"
    v = round(float(v), 1) + 0.0
    return f"{v:+.1f}%" if v else "0.0%"


def f_won(v) -> str:
    return f"{v:,.0f}" if v is not None else "–"


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
    env.filters.update(pct=f_pct, won=f_won, ftime=f_time, fdt=f_dt, tab=cat_tab, hp=f_hp)
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
    orgs = list(dict.fromkeys(s["org"] for s in cfg["sources"]))
    base_url = (os.environ.get("SITE_BASE_URL") or os.environ.get("CI_PAGES_URL") or cfg["site"].get("base_url") or "").rstrip("/")

    env = make_env()
    ctx = dict(
        site=cfg["site"], now=now, built=now.strftime("%Y.%m.%d %H:%M"), built_iso=now.isoformat(),
        nav=NAV, categories=CATEGORIES, tab_counts=tab_counts, license_label=LICENSE_LABEL,
        items=items, days=days, home_days=home_days, top5=t5, trend=trend, trend_hours=trend_hours,
        overnight=overnight, tickers=tickers, warn=warn, active=active,
        pest=_current(pests, now), weekly=_current(weeklies, now), pests=pests,
        latest_by_board=latest_by_board, status=status, failed=failed, orgs=orgs,
        sources_cfg={s["id"]: s for s in cfg["sources"]}, base_url=base_url,
        release_cats=RELEASE_CATS,
        top5_js=[{"h": it.headline, "u": f"n-{it.id}.html"} for it in t5],
    )
    SITE.mkdir(exist_ok=True)
    brief_md = env.get_template("brief.md").render(**ctx)
    (SITE / "brief.md").write_text(brief_md, encoding="utf-8")
    (SITE / "brief.txt").write_text(brief_md, encoding="utf-8")
    ctx["brief_text"] = brief_md
    (SITE / "feed.xml").write_text(env.get_template("feed.xml").render(**ctx), encoding="utf-8")
    for page, _ in NAV:
        (SITE / page).write_text(env.get_template(page).render(page=page, **ctx), encoding="utf-8")
    (SITE / "404.html").write_text(env.get_template("404.html").render(page="404.html", **ctx), encoding="utf-8")
    for old in SITE.glob("n-*.html"):
        old.unlink()
    tpl = env.get_template("item.html")
    for it in items:
        (SITE / f"n-{it.id}.html").write_text(
            tpl.render(page="news.html", it=it, related=_related(it, items), **ctx), encoding="utf-8")
    (SITE / "data").mkdir(exist_ok=True)
    for name in ("items.json", "status.json", "tickers.json"):
        p = DATA / name
        if p.exists():
            shutil.copy(p, SITE / "data" / name)
    print(f"built {len(NAV)} pages + {len(items)} item pages")
