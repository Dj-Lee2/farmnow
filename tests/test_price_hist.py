"""공공데이터포털 aT 일별 기록(price_hist)."""
from datetime import datetime
from farmnow.collectors import price_hist as ph


def _i(d, p, unit="포기", sz="1"):
    return {"exmn_ymd": d, "exmn_dd_prc": str(p), "unit": unit, "unit_sz": sz}


def test_day_means_averages_markets_with_same_unit():
    got = ph.day_means([_i("20261119", 2000), _i("20261119", 2706), _i("20261119", 9999, unit="kg", sz="10"), _i("20261118", 2390)], "1포기")
    assert got == {"20261119": 2353, "20261118": 2390}


def test_monthly_rows_and_weekly():
    d = {"20251103": 100, "20251104": 200, "20251201": 300, "20261116": 50, "20261119": 70}
    rows = {r["yyyy"]: r for r in ph.monthly_rows(d)}
    assert rows["2025"]["m11"] == "150" and rows["2025"]["m12"] == "300" and rows["2026"]["m11"] == "60"
    assert ph.weekly(d, "20260101") == [["2026-11-16", 60]]


def test_update_adds_today_and_backfills_within_budget(tmp_path, monkeypatch):
    calls = []

    def fake(q, start, end, key, budget):
        calls.append((start, end)); budget[0] -= 1
        return [_i(end, 1000), _i(start, 900)]
    monkeypatch.setattr(ph, "fetch_range", fake)
    rows = [{"key": "e200202b", "name": "배추/가을", "cls": "소매", "unit": "1포기", "price": 2353.0, "day": "2026-11-19",
             "q": {"ctgry": "200", "item": "211", "vrty": "03", "grd": "04", "se": "01", "unit": "1포기"}}]
    now = datetime(2026, 11, 20)
    out = ph.update(rows, tmp_path / "h.json", "k", now, max_calls=3)
    s = out["series"]["e200202b"]
    assert s["d"]["20261119"] == 2353 and len(calls) == 3 and not s["done"]
    assert calls[0][1] == "20261119" and s["back"] == calls[-1][0]
    out = ph.update(rows, tmp_path / "h.json", None, now)          # 키가 없으면 오늘 값만
    assert out["progress"]["calls"] == 0


def test_redact_hides_keys_in_error_text():
    from farmnow.util import redact
    msg = "503 for url: https://apis.data.go.kr/x?serviceKey=abc%2Bdef&pageNo=1 and kosis apiKey=XYZ123' p_cert_key=k1&p_cert_id=7"
    out = redact(msg)
    assert "abc" not in out and "XYZ123" not in out and "k1" not in out and "pageNo=1" in out and out.count("***") == 4
