from farmnow import homeviews as hv


def test_squarify_fills_area():
    rects = hv.squarify([(3, "a"), (2, "b"), (1, "c")], 0, 0, 300, 200)
    assert sorted(r[0] for r in rects) == ["a", "b", "c"]
    assert abs(sum(r[3] * r[4] for r in rects) - 300 * 200) < 1e-6


def test_enrich_adds_period_changes_and_icon():
    r = hv.enrich({"base": "사과", "price": 110.0, "prev": 100.0, "month_ago": 100.0, "year_ago": 220.0, "pct": 10.0,
                   "season": {"cur": [100.0, None] + [None] * 10, "avg": [80.0] + [None] * 11}})
    assert r["e"] == "🍎" and round(r["m"]) == 10 and round(r["y"]) == -50 and round(r["n"]) == 25


def test_treemap_one_tile_per_item():
    rows = [hv.enrich({"base": b, "disp": d, "category": c, "price": 1.0, "pct": 0.0, "key": d})
            for b, d, c in [("소", "소 등심", "축산물"), ("소", "소 갈비", "축산물"), ("배추", "배추", "채소류")]]
    tm = hv.treemap(rows, 400, 200)
    assert tm["n"] == 2 and sum(len(g["tiles"]) for g in tm["groups"]) == 2
