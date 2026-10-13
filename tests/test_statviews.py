from farmnow import statviews as sv


def _s(title, vals, unit="톤"):
    return {"title": title, "unit": unit, "years": [str(2020 + i) for i in range(len(vals))], "vals": vals}


def test_item_view_groups_province_series():
    K = {"fr_apple": _s("사과 생산량", [1, 2]), "ia_inc_apl": _s("사과 10a당 소득", [5, 6], "원"),
         "cfx_apple": _s("소비자물가 사과", [100, 101], "2020=100"), "x": _s("배추 생산량", [1, 2])}
    for c, n in sv.PROV[:5]:
        K[f"oap_{c}"] = _s(f"사과 재배면적({c})", [10, 20], "ha")
    v = sv.item_view(K, sv.ITEMS[10])
    sec = dict(v["sections"])
    assert [k for k, _ in sec["생산·면적"]] == ["fr_apple"]
    assert [k for k, _ in sec["가격"]] == ["cfx_apple"] and [k for k, _ in sec["돈"]] == ["ia_inc_apl"]
    assert sec["지역"][0]["title"] == "사과 재배면적" and len(sec["지역"][0]["rows"]) == 5
    assert "배추" not in str(sec)


def test_item_name_needs_word_boundary():
    K = {"a": _s("배추 생산량", [1, 2]), "b": _s("배 생산량", [1, 2])}
    v = sv.item_view(K, next(i for i in sv.ITEMS if i[0] == "pear"))
    assert [k for k, _ in dict(v["sections"])["생산·면적"]] == ["b"]


def test_region_view_needs_eight_provinces():
    K = {f"ir_{c}": _s(f"{n} 농가소득", [100, 110], "천원") for c, n in sv.PROV}
    r = sv.region_view(K)
    assert [i["id"] for i in r["inds"]] == ["ir"] and r["inds"][0]["vals"]["gb"]["p"] == 10.0
