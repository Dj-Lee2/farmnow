import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup

from farmnow import build
from farmnow.collectors.kma_warn import regions_short, _actions
from farmnow.collectors.mafra_rss import excerpt
from farmnow.collectors.nongsaro_list import _PERIOD, _view_link
from farmnow.model import NewsItem, gate
from farmnow.rules import tag, top5, tag_trend, parse_status_t6, warning_kinds, overnight_summary

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=KST)
ROOT = Path(__file__).resolve().parent.parent
DICTS = yaml.safe_load((ROOT / "config" / "dicts.yml").read_text(encoding="utf-8"))


def _item(**kw):
    base = dict(source_id="s", source_name="테스트", org="기관", url="https://example.org/1", headline="제목",
                event_time=NOW, time_precision="minute", fetched_at=NOW, category="org_release", label="발표", license="kogl1")
    base.update(kw)
    return NewsItem(**base)


# ---- 게시 게이트 ----
def test_closed_license_drops_body_but_keeps_item():
    it = _item(license="unknown", body="본문 발췌", body_mode="excerpt")
    assert gate(it) == (True, "ok")
    assert it.body == "" and it.body_mode == "title_only" and "license_unknown" in it.quality_flags


def test_open_license_keeps_excerpt():
    it = _item(license="kogl1", body="본문 발췌", body_mode="excerpt")
    assert gate(it)[0] and it.body == "본문 발췌"


def test_banned_word_blocks_template_but_not_quoted_title():
    assert gate(_item(generator="template", body_mode="template", body="가격 폭등 전망이다"))[0] is False
    assert gate(_item(headline="수급 동향 및 전망 발표"))[0] is True


def test_pii_in_body_drops_body_only():
    it = _item(body="문의 010-1234-5678", body_mode="excerpt")
    assert gate(it)[0] and it.body == "" and "pii_dropped" in it.quality_flags
    assert gate(_item(headline="문의 hong@example.go.kr"))[0] is False
    assert gate(_item(headline="공고 제2026-010-1234-5678호"))[0] is True   # 문서번호는 전화번호가 아니다


def test_uid_makes_id_stable_when_title_changes():
    a, b = _item(uid="579308", headline="제목"), _item(uid="579308", headline="제목(수정)")
    assert a.id == b.id and a.id != _item(uid="579309").id


def test_roundtrip_ignores_unknown_keys():
    d = _item(uid="1", extra={"no": "제1호"}).to_dict()
    d["someday_new_field"] = 1
    back = NewsItem.from_dict(d)
    assert back.extra == {"no": "제1호"} and back.event_time == NOW


# ---- 태그(실제 사전) ----
def test_real_dict_negative_titles():
    for title in ["농업 대전환 전략 발표", "홍콩 수출 상담회 개최", "정밀농업 기술 밀양 시연회", "가축분뇨 부산물 활용",
                  "경기 침체 대응 방안", "국민체험단 모집 공고", "스마트 HACCP 현장 구축"]:
        it = _item(headline=title)
        tag(it, DICTS)
        assert it.tags_items == [] and it.tags_regions == [], title
        assert "#지원사업" not in it.tags_events and "#스마트농업" not in it.tags_events, title


def test_real_dict_positive_titles():
    it = _item(headline="전남 배추 수급 점검, 우리밀 수매 확대")
    tag(it, DICTS)
    assert set(it.tags_items) == {"배추", "밀"} and it.tags_regions == ["전남"] and "#수급" in it.tags_events
    it = _item(headline="농어촌 기본소득 시범사업 점검")
    tag(it, DICTS)
    assert "#기본소득" in it.tags_events and "#직불금" not in it.tags_events


def test_trend_queries_match_item_text():
    items = [_item(headline=f"배추 수급 {i}", url=f"https://e.org/{i}") for i in range(3)]
    for it in items:
        tag(it, DICTS)
    trend, hours = tag_trend(items, NOW)
    assert hours == 24 and {t["q"] for t in trend} >= {"배추", "#수급"}
    text = " ".join(items[0].tags_items + items[0].tags_events)
    assert all(t["q"] in text for t in trend)          # 검색어가 항목의 검색 텍스트에 실제로 들어 있다
    assert all(t["label"].startswith("#") for t in trend)


# ---- 특보 상태·오늘의 핵심 ----
def test_status_parser():
    assert parse_status_t6("o 없 음") == []
    got = parse_status_t6("o 호우주의보 : 경상북도(경주동부)\r\no 강풍주의보 : 제주도(추자도)")
    assert [g["kind"] for g in got] == ["호우주의보", "강풍주의보"] and got[0]["regions"] == "경상북도(경주동부)"
    assert warning_kinds("강풍주의보·호우경보 해제") == {"강풍주의보", "호우경보"}


def test_top5_drops_released_and_inactive_warnings():
    issued = _item(category="weather", label="특보", severity="warning", headline="기상청, 호우주의보 발표 — 경북",
                   event_time=NOW - timedelta(hours=3), url="https://e.org/w1")
    released = _item(category="weather", label="특보 해제", headline="기상청, 호우주의보 해제 — 경북",
                     event_time=NOW - timedelta(hours=2), url="https://e.org/w2")
    news = _item(headline="보도자료", url="https://e.org/n")
    assert top5([issued, released, news], NOW, active_kinds=set()) == [news]
    assert issued in top5([issued, released, news], NOW, active_kinds={"호우주의보"})
    assert released not in top5([issued, released, news], NOW, active_kinds=None)


def test_top5_excludes_old_date_only_docs_and_caps_price():
    old = _item(time_precision="date", event_time=NOW - timedelta(days=28), severity="alert", category="pest")
    prices = [_item(category="price", label="급변", severity="advisory", headline=f"p{i}", url=f"https://e.org/p{i}") for i in range(3)]
    fresh = _item(headline="새 항목", url="https://e.org/2")
    picked = top5([old, fresh, *prices], NOW)
    assert old not in picked and fresh in picked and sum(1 for p in picked if p.category == "price") == 1


def test_overnight_buckets_sum_to_total():
    items = [_item(category=c, url=f"https://e.org/{i}") for i, c in enumerate(
        ["weather", "pest", "subsidy_notice", "legislation", "org_release", "stats", "price", "tech_research"])]
    items.append(_item(event_time=NOW - timedelta(hours=30), url="https://e.org/old"))
    o = overnight_summary(items, NOW, {"surge_count": 5, "day": "2026-09-29"})
    assert o["total"] == 8 == o["weather"] + o["pest"] + o["notice"] + o["release"] + o["price"] + o["other"]
    assert o["surge"] == 5


# ---- 수집기 파서 ----
def test_kma_actions_and_regions():
    acts = _actions("(1) 호우주의보 발표 : 경상북도(경주동부)\r\n(2) 강풍주의보 해제 : 전라남도(거문도.초도), 제주도(추자도)")
    assert acts[0] == ("호우주의보 발표", "경상북도(경주동부)") and acts[1][0] == "강풍주의보 해제"
    assert regions_short("경상북도(경주동부), 전라남도(거문도), 제주도남쪽먼바다") == ["전남", "경북", "제주", "해상"]


def test_excerpt_takes_first_sentences_of_first_long_paragraph():
    html = ("<div class='view_contents'><p>보도자료</p><p><span>  농림축산식품부는 </span><span>9</span><span>월 </span><span>30</span>"
            "<span>일 가을배추 수급 안정 대책을 발표했다.</span><span> 이번 대책에는 비축 물량 방출과 할인 지원이 포함된다.</span>"
            "<span> 세 번째 문장은 나오지 않아야 한다.</span></p></div>")
    out = excerpt(BeautifulSoup(html, "html.parser").select_one(".view_contents"))
    assert out.startswith("농림축산식품부는 9월 30일 가을배추") and "세 번째" not in out and len(out) <= 200


def test_nongsaro_period_and_view_link():
    for title, want in [("병해충발생정보 제12호 (2026.9.1~9.30)", ("2026", "9", "1", "9", "30")),
                        ("주간농사정보 제39호 (2026. 9. 28.~10. 4.)", ("2026", "9", "28", "10", "4")),
                        ("주간농사정보 제40호 (2026.10.5.~10.11.)", ("2026", "10", "5", "10", "11"))]:
        assert _PERIOD.search(title).groups() == want
    assert _view_link("onclick=\"fncFileView('270135', '185001', this)\"", "x")[0].endswith("cntntsNo=270135&fileSeCode=185001&fileSn=1")
    assert _view_link("onclick=\"fncFileDown('270537', '1', '185001', this)\"", "x") == (
        "https://www.nongsaro.go.kr/portal/contentsFileView.do?cntntsNo=270537&fileSeCode=185001&fileSn=1", "270537")
    assert _view_link("<tr></tr>", "fallback") == ("fallback", None)


# ---- 빌드 ----
CFG = {"site": {"name": "팜나우", "tagline": "t", "keep_days": 14, "surge_threshold_pct": 10, "stale_minutes": 180},
       "sources": [{"id": "s", "name": "테스트", "org": "기관", "url": "https://example.org"},
                   {"id": "kma_warn", "name": "기상청", "org": "기상청", "url": "https://example.org", "home_url": "https://example.org"}]}


def _build(tmp_path, monkeypatch, records):
    monkeypatch.setattr(build, "SITE", tmp_path / "site")
    monkeypatch.setattr(build, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    build.build_site(CFG, records, {"s": {"name": "테스트", "ok": True, "count": len(records), "published": len(records)}}, NOW, None)
    return tmp_path / "site"


def test_build_survives_without_tickers_or_items(tmp_path, monkeypatch):
    site = _build(tmp_path, monkeypatch, [])
    for page, _ in build.NAV:
        assert (site / page).stat().st_size > 1000
    assert "시세 미수집" in (site / "market.html").read_text(encoding="utf-8")


def test_feed_is_wellformed_with_special_chars_and_item_pages_exist(tmp_path, monkeypatch):
    it = _item(headline='농식품 R&D <2026> "성과" 발표', body="A&B < C", body_mode="excerpt", uid="1")
    site = _build(tmp_path, monkeypatch, [it.to_dict()])
    root = ET.fromstring((site / "feed.xml").read_text(encoding="utf-8"))
    assert root.find("channel/item/title").text == '[발표] 농식품 R&D <2026> "성과" 발표'
    assert (site / f"n-{it.id}.html").exists()
    assert f'href="n-{it.id}.html"' in (site / "news.html").read_text(encoding="utf-8")


def test_stylesheet_uses_rem_so_large_text_works():
    import re
    css = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert not re.search(r"font-size:\d+px", css.replace("html{font-size:14px", "").replace("html.big{font-size:17px", ""))


# ---- 재점검에서 나온 결함의 회귀 테스트 ----
from farmnow.collectors import mafra_rss, kamis


class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content, self._payload, self.text, self.encoding = content, payload, "", "utf-8"

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _rss(*entries):
    body = "".join(f"<item><title>{t}</title><link>http://www.mafra.go.kr/bbs/home/792/{n}/artclView.do</link>"
                   f"<pubDate>{d}</pubDate></item>" for n, t, d in entries)
    return _Resp(f"<rss version='2.0'><channel>{body}</channel></rss>".encode())


SRC = {"id": "mafra_792", "name": "농식품부 보도자료", "org": "농림축산식품부", "url": "https://x/rss", "category": "org_release",
       "label": "발표", "backfill_pages": 2}


def test_mafra_new_item_is_held_when_detail_fails_or_release_is_future(monkeypatch):
    monkeypatch.setattr(mafra_rss, "now_kst", lambda: NOW)
    monkeypatch.setattr(mafra_rss.time, "sleep", lambda s: None)
    monkeypatch.setattr(mafra_rss.requests, "get", lambda *a, **k: _rss((1, "공모전 수상작 선정", "2026-09-30 09:00:00.0")) if (k.get("params") or {}).get("page") == 1 else _rss())
    monkeypatch.setattr(mafra_rss, "fetch_detail", lambda url: (_ for _ in ()).throw(TimeoutError()))
    assert mafra_rss.collect(SRC, {}, 14) == []                       # 원문 확인 실패 → 게시하지 않음
    monkeypatch.setattr(mafra_rss, "fetch_detail", lambda url: {"date": NOW + timedelta(hours=3), "has_time": True})
    assert mafra_rss.collect(SRC, {}, 14) == []                       # 배포 예정 → 보류
    monkeypatch.setattr(mafra_rss, "fetch_detail", lambda url: {"date": NOW - timedelta(hours=1), "has_time": True, "license": "kogl1", "excerpt": "발췌."})
    got = mafra_rss.collect(SRC, {}, 14)
    assert len(got) == 1 and got[0].label == "발표" and got[0].body == "발췌." and got[0].time_source == "page"


def test_mafra_backfills_next_page_until_no_fresh_items(monkeypatch):
    pages = []
    monkeypatch.setattr(mafra_rss, "now_kst", lambda: NOW)
    monkeypatch.setattr(mafra_rss.time, "sleep", lambda s: None)
    monkeypatch.setattr(mafra_rss, "fetch_detail", lambda url: {"date": NOW - timedelta(hours=5), "has_time": True})

    def get(url, params=None, **k):
        pages.append(params["page"])
        return _rss((10 + params["page"], f"제목 {params['page']}", "2026-09-29 09:00:00.0"))
    monkeypatch.setattr(mafra_rss.requests, "get", get)
    got = mafra_rss.collect(SRC, {"old": {"source_id": "mafra_792", "uid": "5"}}, 14)
    assert pages == [1, 2] and len(got) == 2                          # 이력이 있어도 새 글이 있으면 다음 쪽까지 읽는다


def test_excerpt_skips_headings_contacts_and_unbalanced_quotes():
    def ex(html):
        return excerpt(BeautifulSoup(f"<div class='v'>{html}</div>", "html.parser").select_one(".v"))
    assert ex("<p>&lt;주요 보도 내용&gt; 어떤 매체가 다음과 같이 보도하였다는 내용이 길게 이어지는 제목 줄입니다.</p>") == ""
    assert ex("<p>□ 담당자: 농림축산식품부 ○○과 홍길동 주무관(044-201-1234)에게 문의하여 주시기 바랍니다.</p>") == ""
    assert ex("<p>농림축산식품부는 김장철을 앞두고 가을배추 수급 안정 대책을 마련하여 이번 주부터 시행한다고 밝혔다.<br>□ 세부 계획: 비축 물량 방출</p>").endswith("밝혔다.")
    assert ex("<p>해당 기사는 ‘정책 효과가 미미하다. 또한 예산이 부족하다’는 취지로 작성되었으나 이는 사실과 다릅니다.</p>").endswith("다릅니다.")
    assert ex("<p>농림축산식품부는 보조사업자를 다음과 같이 공모하니 관심 있는 기관의 많은 참여를 바라며,</p>") == ""


def test_kamis_surge_ids_unique_and_keys_stable(monkeypatch):
    def rows(extra=()):
        base = [("0", "닭/육계", "축산물", 125.0, 100.0), ("0", "돼지/갈비", "축산물", 70.0, 100.0), ("9", "쌀/20kg", "식량작물", 100.0, 100.0)]
        return [{"productno": n, "productName": nm, "product_cls_name": "소매", "category_name": c, "unit": "1kg",
                 "dpr1": str(p), "dpr2": str(q), "dpr3": "-", "dpr4": "-", "lastest_day": "2026-09-30"} for n, nm, c, p, q in [*extra, *base]]
    src = {"id": "kamis_daily", "name": "KAMIS", "org": "aT", "url": "https://x"}
    monkeypatch.setattr(kamis.requests, "get", lambda *a, **k: _Resp(payload={"error_code": "000", "condition": [["20260930"]], "price": rows()}))
    items, t1 = kamis.collect(src)
    surge = [i for i in items if i.label == "급변"]
    assert len({i.id for i in surge}) == len(surge) == 2
    monkeypatch.setattr(kamis.requests, "get", lambda *a, **k: _Resp(payload={"error_code": "000", "condition": [["20260930"]],
                                                                          "price": rows([("3", "가지/가지", "채소류", 10.0, 10.0)])}))
    _, t2 = kamis.collect(src)
    key = {r["name"]: r["key"] for r in t1["all"]}
    assert all(key[r["name"]] == r["key"] for r in t2["all"] if r["name"] in key)      # 행 순서가 바뀌어도 키는 같다
    monkeypatch.setattr(kamis.requests, "get", lambda *a, **k: _Resp(payload={"error_code": "000", "condition": [["20260930"]], "price": []}))
    try:
        kamis.collect(src)
        assert False, "빈 응답은 실패로 처리해야 한다"
    except RuntimeError:
        pass


def test_build_ignores_items_of_removed_sources_and_hides_stale_warning(tmp_path, monkeypatch):
    ghost = _item(source_id="mafra_999", uid="9").to_dict()
    monkeypatch.setattr(build, "SITE", tmp_path / "site")
    monkeypatch.setattr(build, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    stale = {"as_of": NOW.isoformat(), "seq": 1, "active": [{"kind": "호우주의보", "regions": "경상북도", "short": ["경북"]}], "stale": True}
    build.build_site(CFG, [ghost, _item(uid="1").to_dict()], {"s": {"name": "테스트", "ok": True, "count": 1, "published": 1}}, NOW, stale)
    home = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "확인 불가" in home and "발효 중" not in home
    assert not list((tmp_path / "site").glob(f"n-{NewsItem.from_dict(ghost).id}.html"))


def test_hangul_only_strips_hanja():
    from farmnow.util import hangul_only
    assert hangul_only("강화 소재 소(牛) 농장") == "강화 소재 소 농장"
    assert hangul_only("농식품으로 정(情)을 나누다") == "농식품으로 정을 나누다"
    assert hangul_only("전일比 -18.5%") == "전일 대비 -18.5%"
    assert hangul_only("韓牛 수급") == " 수급"


def test_naver_market_collect_signs_and_sections(monkeypatch):
    from farmnow.collectors import naver_market as nm
    monkeypatch.setattr(nm, "PAUSE", 0)

    class R:
        def __init__(self, j, code=200): self._j, self.status_code = j, code
        def json(self): return self._j
    kr = [{"localTradedAt": "2026-09-30", "closePrice": "6,080", "compareToPreviousClosePrice": "20", "compareToPreviousPrice": {"name": "FALLING"}, "fluctuationsRatio": "-0.33", "accumulatedTradingVolume": "1000"},
          {"localTradedAt": "2026-09-29", "closePrice": "6,100", "compareToPreviousClosePrice": "0", "fluctuationsRatio": "0"}]
    wd = [{"localTradedAt": "2026-09-29T16:00:00-04:00", "closePrice": "680.50", "compareToPreviousClosePrice": "-9.09", "fluctuationsRatio": "-1.32"}]
    cm = {"result": [{"localTradedAt": "2026-09-29T16:00:00-05:00", "closePrice": "522.00", "fluctuations": "-1.00", "fluctuationsType": {"name": "FALLING"}, "fluctuationsRatio": "-0.19"}]}
    fx = {"result": [{"localTradedAt": "2026-09-30", "closePrice": "1,354.70", "fluctuations": "1.20", "fluctuationsType": {"name": "RISING"}, "fluctuationsRatio": "0.09"}]}
    def fake(url, params=None, headers=None, timeout=None):
        if "marketIndex/prices" in url: return R(fx if params.get("category") == "exchange" else cm)
        if "api.stock.naver.com" in url: return R(wd) if "/DE/" in url else R({"code": "StockConflict"}, 409)
        return R(kr)
    monkeypatch.setattr(nm.requests, "get", fake)
    src = {"kr_stocks": [{"name": "남해화학", "code": "025860", "group": "비료"}],
           "world_stocks": [{"name": "디어", "code": "DE", "group": "농기계"}, {"name": "없음", "code": "ZZZ", "group": "농기계"}],
           "commodities": [{"name": "옥수수", "code": "Ccv1", "unit": "USc/BSH", "group": "곡물"}]}
    items, d = nm.collect(src)
    k, w, c = d["sections"]["kr_stocks"][0], d["sections"]["world_stocks"][0], d["sections"]["commodities"][0]
    assert items == [] and k["chg"] == -20.0 and k["spark"] == [6100.0, 6080.0] and k["cur"] == "KRW"
    assert (w["price"], w["chg"], w["day"], w["cur"]) == (680.5, -9.09, "2026-09-29", "USD")
    assert (c["chg"], c["unit"]) == (-1.0, "센트/부셸") and d["usdkrw"]["price"] == 1354.7
    assert len(d["missing"]) == 1 and d["day"] == "2026-09-30"



def test_market_detail_parse_and_enrich(monkeypatch, tmp_path):
    from farmnow.collectors import naver_market as nm
    from farmnow import build
    monkeypatch.setattr(nm, "PAUSE", 0)

    class R:
        def __init__(self, j): self._j, self.status_code = j, 200
        def json(self): return self._j
    kr_int = {"totalInfos": [{"key": "시총", "value": "1,189억"}, {"key": "PER", "value": "9.17배"}, {"key": "52주 최고", "value": "8,980"},
                             {"key": "52주 최저", "value": "6,400"}, {"key": "EPS", "value": "N/A"}],
              "dealTrendInfos": [{"bizdate": "20260930", "closePrice": "7,420", "compareToPreviousClosePrice": "370",
                                  "compareToPreviousPrice": {"name": "RISING"}, "foreignerPureBuyQuant": "+19,699"}]}
    chart = [{"localDate": "20250930", "closePrice": 8800.0, "accumulatedTradingVolume": 100},
             {"localDate": "20260929", "closePrice": 7050.0, "accumulatedTradingVolume": 90}]
    news = [{"items": [{"titleFull": "종자사업 통합", "officeName": "뉴시스", "datetime": "202609301402", "mobileNewsUrl": "https://n.news.naver.com/x"}]}]
    cm_rows = [{"localTradedAt": "2026-09-29T16:00:00-05:00", "closePrice": "522.00"}, {"localTradedAt": "2026-08-29T16:00:00-05:00", "closePrice": "537.00"}]
    def fake(url, params=None, headers=None, timeout=None):
        if "integration" in url: return R(kr_int)
        if "/chart/domestic/" in url: return R(chart)
        if "/news/stock/" in url: return R(news)
        if "productDetail" in url: return R({"result": {"stockExchangeType": {"nameKor": "시카고상품거래소"}, "month": "26.12.", "unit": "USc/BSH"}})
        if "marketIndex/prices" in url: return R({"result": cm_rows})
        raise AssertionError(url)
    monkeypatch.setattr(nm.requests, "get", fake)
    now = datetime(2026, 9, 30, 18, 0, tzinfo=NOW.tzinfo)
    d = nm.fetch_detail("kr_stocks", {"code": "054050", "name": "NH농우바이오", "group": "종자"}, now)
    assert [i["k"] for i in d["infos"]] == ["시총", "PER", "52주 최고", "52주 최저"]   # N/A는 뺀다
    assert d["hist"][0] == {"d": "2025-09-30", "c": 8800.0, "o": None, "h": None, "l": None, "v": 100}
    assert d["trend"][0]["d"] == "09.30" and d["news"][0]["dt"] == "09.30"
    c = nm.fetch_detail("commodities", {"code": "Ccv1", "name": "옥수수", "group": "곡물"}, now)
    assert c["unit"] == "센트/부셸" and [h["d"] for h in c["hist"]] == ["2026-08-29", "2026-09-29"]

    mdir = tmp_path / "market"; mdir.mkdir()
    (mdir / "054050.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(build, "DATA", tmp_path)
    st = build.enrich_market({"sections": {"kr_stocks": [{"code": "054050", "name": "NH농우바이오", "group": "종자", "cur": "KRW", "price": 7420.0, "pct": 5.25, "chg": 370.0, "spark": [7050.0, 7420.0], "day": "2026-09-30"}]}}, now)
    r = st["sections"]["kr_stocks"][0]
    assert r["slug"] == "s-054050.html" and r["cap"] == "1,189억" and r["per"] == "9.17배"
    assert (r["hi52"], r["lo52"], r["pos52"]) == (8980.0, 6400.0, 40) and r["r1y"] == round((7420 - 8800) / 8800 * 100, 2)
