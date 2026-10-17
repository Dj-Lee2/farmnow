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
