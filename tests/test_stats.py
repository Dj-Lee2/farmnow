"""홈 농업 통계: KOSIS 행 고르기·캐시와 KAMIS 1년 전 대비 계산."""
import json
from datetime import timedelta

from farmnow import build
from farmnow.collectors import kosis
from farmnow.util import now_kst


def _r(prd, itm, dt, unit="", **cls):
    return {"PRD_DE": prd, "ITM_NM": itm, "DT": dt, "UNIT_NM": unit, "TBL_NM": "표", **cls}


def test_pick_series_totals_only():
    rows = [_r("2023", "농가", "999,022", "가구", C1_NM="전국"), _r("2024", "농가", "974,245", "가구", C1_NM="전국"),
            _r("2024", "농가", "12,000", "가구", C1_NM="서울특별시"), _r("2024", "농가인구", "2,000,000", "명", C1_NM="전국")]
    s = kosis.pick_series(rows, "^농가(?!인구)")
    assert s["years"] == ["2023", "2024"] and s["vals"] == [999022.0, 974245.0] and s["unit"] == "가구"
    assert kosis.pick_series(rows, "농가인구")["vals"] == [2000000.0]


def test_pick_series_want_class_and_shortest_item():
    rows = [_r("2024", "생산량", "3,585,000", "톤", C1_NM="계", C2_NM="미곡"),
            _r("2024", "10a당 생산량", "514", "kg", C1_NM="계", C2_NM="미곡"),
            _r("2024", "생산량", "90,000", "톤", C1_NM="계", C2_NM="맥류"),
            _r("2024", "생산량", "700,000", "톤", C1_NM="전라남도", C2_NM="미곡"),
            _r("2025", "생산량", "-", "톤", C1_NM="계", C2_NM="미곡")]
    s = kosis.pick_series(rows, "생산량", ["^(미곡|쌀|논벼)"])
    assert s["years"] == ["2024"] and s["vals"] == [3585000.0]


def test_pick_series_class_as_item():
    rows = [_r("2024", "계", "1,512,000", "ha", C1_NM="전국"), _r("2024", "논", "764,000", "ha", C1_NM="전국"),
            _r("2024", "밭", "748,000", "ha", C1_NM="전국")]
    assert kosis.pick_series(rows, "면적|^계$")["vals"] == [1512000.0]
    assert kosis.pick_series(rows, "논|면적", ["^논"])["vals"] == [764000.0]


def test_pick_series_by_class_code():
    rows = [_r("2023", "배출량", "707.2", "백만t", C1="A.4300003", C1_NM="소계"),
            _r("2023", "배출량", "22.5", "백만t", C1="A.4300081", C1_NM="소계"),
            _r("2023", "배출량", "6.7", "백만t", C1="A.4300040", C1_NM="소계")]
    assert kosis.pick_series(rows, "^배출량$", codes={"C1": "4300081$"})["vals"] == [22.5]
    assert kosis.pick_series(rows, "^배출량$", codes={"C1": "43000(40|81)$"}, total=True)["vals"] == [29.2]


def test_collect_uses_cache_and_skips_without_key(tmp_path, monkeypatch):
    src = {"id": "k", "env_key": "KOSIS_TEST_KEY", "series": [{"id": "farms", "title": "농가 수", "tbl": "T", "itm": "농가"}]}
    monkeypatch.delenv("KOSIS_TEST_KEY", raising=False)
    try:
        kosis.collect(src, tmp_path)
        raise AssertionError("키 없음이어야 함")
    except RuntimeError as e:
        assert str(e) == "no_api_key"
    old = {"fetched_at": (now_kst() - timedelta(hours=30)).isoformat(), "series": {"farms": {"vals": [1.0], "years": ["2024"]}}}
    (tmp_path / "stats.json").write_text(json.dumps(old), encoding="utf-8")
    assert kosis.collect(src, tmp_path)[1] == old            # 키가 없으면 지난 값을 그대로


def test_collect_fetches_and_keeps_prev_on_partial_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("KOSIS_TEST_KEY", "x")
    src = {"id": "k", "env_key": "KOSIS_TEST_KEY", "series": [
        {"id": "farms", "title": "농가 수", "tbl": "A", "itm": "^농가$", "survey": "농림어업조사"},
        {"id": "rice", "title": "쌀 생산량", "tbl": "B", "itm": "생산량", "want": ["미곡"]}]}
    prev = {"fetched_at": (now_kst() - timedelta(days=3)).isoformat(), "series": {"rice": {"vals": [9.0], "years": ["2023"]}}}
    (tmp_path / "stats.json").write_text(json.dumps(prev), encoding="utf-8")

    def fake(key, tbl, org="101", years=10, max_dims=4, se="Y"):
        if tbl == "B":
            raise RuntimeError("KOSIS B err=30")
        return [_r("2024", "농가", "974,245", "가구", C1_NM="전국")]
    monkeypatch.setattr(kosis, "fetch_table", fake)
    _, st = kosis.collect(src, tmp_path)
    assert st["series"]["farms"]["vals"] == [974245.0] and st["series"]["farms"]["survey"] == "농림어업조사"
    assert st["series"]["rice"] == prev["series"]["rice"]
    assert st["fetched_at"] == prev["fetched_at"]            # 일부 실패면 다음 회차에 다시 받는다


def test_price_stats_year_over_year():
    def row(name, base, cat, price, year_ago, cls="소매", idx=0):
        return {"name": name, "disp": name, "base": base, "category": cat, "cls": cls, "price": price, "year_ago": year_ago,
                "unit": "1kg", "idx": idx, "key": name}
    t = {"day": "2026-10-01", "all": [row("쌀/20kg", "쌀", "식량작물", 60000, 50000), row("배추/", "배추", "채소류", 3000, 6000),
                                      row("무/", "무", "채소류", 2000, 2000), row("배추/도매", "배추", "채소류", 1, 2, cls="도매"),
                                      row("사과/후지", "사과", "과일류", 3000, None)]}
    p = build.price_stats(t)
    assert p["n"] == 3 and p["up"] == 1 and p["down"] == 1
    assert [c["name"] for c in p["cats"]] == ["식량작물", "채소류"]
    assert p["cats"][1]["pct"] == -25.0
    assert [r["base"] for r in p["risers"]] == ["쌀"] and [r["base"] for r in p["fallers"]] == ["배추"]
    assert build.price_stats({"all": []}) == {}


def test_f_man():
    assert build.f_man(974245) == "97.4만" and build.f_man(514) == "514" and build.f_man(None) == "–"


def test_pick_series_quarterly_keeps_full_period():
    from farmnow.collectors.kosis import pick_series
    rows = [{"ITM_NM": "농가판매가격지수", "C1_NM": "총지수", "PRD_DE": "202601", "DT": "123.1"},
            {"ITM_NM": "농가판매가격지수", "C1_NM": "총지수", "PRD_DE": "202602", "DT": "124.4"},
            {"ITM_NM": "농가판매가격지수", "C1_NM": "곡물", "PRD_DE": "202602", "DT": "99"}]
    got = pick_series(rows, "^농가판매가격지수$", ["^총지수$"], se="Q")
    assert got["years"] == ["202601", "202602"] and got["vals"] == [123.1, 124.4]


def test_f_prd():
    from farmnow.build import f_prd
    assert f_prd("2025") == "2025년" and f_prd("202602") == "26년 2분기"


def test_f_big():
    from farmnow.build import f_big
    assert f_big(201271652) == "2.01억" and f_big(3153897) == "315.4만" and f_big(72389) == "72,389" and f_big(1.31) == "1.3" and f_big(4.0) == "4"


def test_pick_series_total_sums_matching_items():
    from farmnow.collectors.kosis import pick_series
    rows = [{"ITM_NM": n, "C1_NM": c, "PRD_DE": "2024", "DT": d, "UNIT_NM": "가구"}
            for n, c, d in [("65~69세", "전국", "10"), ("70~74세", "전국", "5"), ("70~74세", "전국", "5"),
                            ("65~69세", "경기도", "3"), ("계", "전국", "40")]]
    got = pick_series(rows, r"^(65~69세|70~74세)$", total=True)
    assert got["vals"] == [15.0]
    assert pick_series(rows, r"^계$")["vals"] == [40.0]


def test_pick_series_total_sums_regions_when_no_national_row():
    from farmnow.collectors.kosis import pick_series
    rows = [{"ITM_NM": "보유수량", "C1_NM": c, "C2_NM": m, "PRD_DE": "2025", "DT": d}
            for c, m, d in [("경기도", "콤바인", "10"), ("전라남도", "콤바인", "20"), ("가평군", "콤바인", "3"),
                            ("경기도", "콤바인", "10"), ("경기도", "관리기", "99")]]
    got = pick_series(rows, "^보유수량$", ["^콤바인$", "(도|특별시|광역시|특별자치시)$"], total=True)
    assert got["vals"] == [30.0]


def test_f_mini_negative_values_draw_from_zero_line():
    from farmnow.build import f_mini
    svg = str(f_mini([100, -50, -100], "t", "#000"))
    assert "stroke-dasharray" in svg and svg.count('class="b"') == 3
    assert "-100" in svg


def test_f_ratio_matches_periods():
    a = {"title": "a", "tbl": "T", "years": ["2023", "2024"], "vals": [30.0, 40.0], "unit": "가구"}
    b = {"title": "b", "tbl": "T", "years": ["2022", "2024"], "vals": [50.0, 80.0], "unit": "가구"}
    r = build.f_ratio(a, b)
    assert r["years"] == ["2024"] and r["vals"] == [50.0] and r["unit"] == "%" and r["tbl"] == "T"
    assert build.f_ratio(a, None) is None


def test_pick_series_monthly_period_and_label():
    rows = [_r("202606", "도축실적", "78467", C1_NM="당월(A)", C2_NM="소(계)"),
            _r("202607", "도축실적", "74644", C1_NM="당월(A)", C2_NM="소(계)"),
            _r("202607", "도축실적", "88", C1_NM="전년대비(A/C)", C2_NM="소(계)")]
    got = kosis.pick_series(rows, "도축실적$", ["^당월", "^소\\(계\\)$"], "M")
    assert got["years"] == ["2026.06", "2026.07"] and got["vals"] == [78467.0, 74644.0]
    assert build.f_prd("2026.07") == "26년 7월" and build.f_prd("202602") == "26년 2분기"
