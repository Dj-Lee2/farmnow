"""공공데이터포털 aT 가격 응답 → KAMIS 모양 변환."""
from farmnow.collectors import kamis


def _i(**k):
    base = {"exmn_ymd": "20261119", "se_nm": "소매", "ctgry_nm": "채소류", "item_cd": "211", "item_nm": "배추", "vrty_cd": "01",
            "vrty_nm": "가을", "grd_nm": "상품", "unit": "포기", "unit_sz": "1", "exmn_dd_prc": "2353", "dd1_bfr_prc": "2400",
            "mm1_bfr_prc": "3000", "yy1_bfr_prc": "2800"}
    return dict(base, **k)


def test_from_datago_maps_to_kamis_rows_and_keeps_keys():
    j = kamis.from_datago([
        _i(), _i(grd_nm="중품", exmn_dd_prc="1"),                         # 같은 규격은 상품 등급
        _i(se_nm="중도매", unit="kg(그물망 3포기)", unit_sz="10", exmn_dd_prc="8416"),
        _i(exmn_ymd="20111230", item_nm="옛것"),                            # 오래된 조사일 행은 버림
        _i(item_nm="쌀", vrty_nm="20kg", unit="kg", unit_sz="20.0", exmn_dd_prc="63351", dd1_bfr_prc="63351"),
    ])
    assert j["error_code"] == "000" and j["condition"] == [["20261119"]]
    by = {(p["productName"], p["product_cls_name"], p["unit"]): p for p in j["price"]}
    assert set(by) == {("배추/가을", "소매", "1포기"), ("배추/가을", "도매", "10kg(그물망 3포기)"), ("쌀/20kg", "소매", "20kg")}
    assert by[("배추/가을", "소매", "1포기")]["dpr1"] == "2353"


def test_fetch_rows_uses_datago_first(monkeypatch):
    monkeypatch.setattr(kamis, "fetch_datago", lambda src: kamis.from_datago([_i(), _i(item_nm="쌀", vrty_nm="20kg", unit="kg", unit_sz="20")]))
    rows, day = kamis.fetch_rows({"id": "kamis_daily", "exclude_categories": ["수산물"]})
    assert day == "20261119" and {r["name"] for r in rows} == {"배추/가을", "쌀/20kg"}
    assert next(r for r in rows if r["name"] == "배추/가을")["pct"] == -2.0
