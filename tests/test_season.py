from farmnow.collectors.kamis_season import attach, band


def _row(y, vals):
    r = {"yyyy": str(y)}
    r.update({f"m{m + 1}": (f"{v:,}" if v is not None else "-") for m, v in enumerate(vals)})
    return r


def test_band_uses_past_five_years():
    rows = [_row(2026, [100, 110] + [None] * 10)] + [_row(y, [90 + (y - 2021) * 5] * 12) for y in range(2021, 2026)]
    b = band(rows, 2026)
    assert b["cur"][:2] == [100.0, 110.0]
    assert b["lo"][0] == 90 and b["hi"][0] == 110 and b["avg"][0] == 100


def test_band_needs_three_past_years():
    rows = [_row(2026, [100, 110] + [None] * 10), _row(2025, [90] * 12), _row(2024, [95] * 12)]
    assert band(rows, 2026) is None


def test_attach_skips_unit_or_price_mismatch():
    b = {"cur": [100.0] + [None] * 11, "unit": "1kg"}
    tk = {"all": [{"name": "양파/양파", "cls": "소매", "unit": "1kg", "price": 110.0},
                  {"name": "파/쪽파", "cls": "소매", "unit": "1kg", "price": 900.0},
                  {"name": "무/고랭지", "cls": "소매", "unit": "1개", "price": 100.0}]}
    season = {"items": {"양파/양파": b, "파/쪽파": b, "무/고랭지": b}}
    assert attach(tk, season) == 1
    assert "season" in tk["all"][0] and "season" not in tk["all"][1] and "season" not in tk["all"][2]


def test_kamis_reads_fresh_relay(tmp_path):
    import json
    from farmnow.collectors import kamis
    (tmp_path / "kamis_daily.json").write_text(json.dumps({"error_code": "000", "price": [1]}), encoding="utf-8")
    src = {"relay_dir": str(tmp_path), "relay_max_hours": 3, "url": "http://127.0.0.1:9/never"}
    assert kamis.fetch_json(src)["price"] == [1]
    assert kamis.relay_fresh({"relay_max_hours": 3}, "kamis_daily.json") is None
