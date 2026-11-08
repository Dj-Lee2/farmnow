import re

from farmnow import build


def test_stats_groups_match_template():
    tpl = open("templates/stats.html", encoding="utf-8").read()
    ids = re.findall(r'<details class="sg-grp" id="(g-[a-z]+)"', tpl)
    assert ids == [g for g, _ in build.SG_GROUPS]


def test_plain_strips_tags():
    assert build._plain('농가 <small>단위</small>\n 소득') == "농가 단위 소득"


def test_clean_urls():
    from farmnow.util import clean_urls
    h = '<a href="./news.html?tag=x">a</a><a href="index.html">h</a><a href="https://kosis.kr/a.html">k</a>'
    js = "location.href='market.html?pick='+k;go('./stats-g-'+g+'.html#'+h)"
    assert clean_urls(h) == '<a href="./news?tag=x">a</a><a href="./">h</a><a href="https://kosis.kr/a.html">k</a>'
    assert clean_urls(js) == "location.href='market?pick='+k;go('./stats-g-'+g+'#'+h)"


def test_stats_today_picks_biggest_change():
    def s(v):
        return {"title": "x", "unit": "명", "se": "Y", "years": [str(2020 + i) for i in range(len(v))], "vals": v}
    t = build.stats_today({"farms": s([10, 10, 10, 10, 11]), "farm_pop": s([10, 10, 10, 10, 20])})
    assert t["key"] == "farm_pop" and round(t["p"]) == 100 and "가장 높은" in t["rank"]


def test_stats_today_skips_values_below_zero():
    def s(v):
        return {"title": "x", "unit": "만 원", "se": "Y", "years": [str(2016 + i) for i in range(len(v))], "vals": v}
    k1, k2 = build.TODAY_KEYS[:2]
    t = build.stats_today({k1: s([98, -50, -60, -160, -100]), k2: s([10, 10, 10, 10, 12])})
    assert t["key"] == k2                                   # 순수익이 0 아래인 계열은 증감률로 고르지 않는다


def test_nav_merges_alerts_and_brief_into_news():
    pages = [p for p, _ in build.NAV]
    assert "alerts.html" not in pages and "brief.html" not in pages and "news.html" in pages   # 옛 주소는 소식으로 넘기는 쪽만 만든다


def test_sg_segments_split_shape_and_refuse_unknown_layout():
    from farmnow.build import _sg_segments
    card = lambda n: f'<section id="c-x-{n}" class="kp4 sg-c"><div class="t">지표{n}<a href="#">k</a></div></section>'
    html = ('<div class="sg-body"><h2 class="sg-sub" id="x-1">가</h2>\n <div class="sg-g">' + card(1) + card(2) + '</div>\n'
            '<h2 class="sg-sub" id="x-2">나</h2><div class="sg-g">' + card(3) + '</div><div class="sg-g" style="margin-top:16px">' + card(4) + '</div>'
            '</div><script>1</script>')
    segs = _sg_segments(html)
    assert [(s[0], s[3]) for s in segs] == [("x-1", ["c-x-1", "c-x-2"]), ("x-2", ["c-x-3", "c-x-4"])]
    assert html[segs[1][1]:segs[1][2]].endswith('</section></div>')
    bad = html.replace('</div>\n<h2 class="sg-sub" id="x-2">', '</div><p>지도</p><h2 class="sg-sub" id="x-2">')
    assert _sg_segments(bad) == []
