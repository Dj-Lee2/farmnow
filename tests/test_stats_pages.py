import re

from farmnow import build


def test_stats_groups_match_template():
    tpl = open("templates/stats.html", encoding="utf-8").read()
    ids = re.findall(r'<details class="sg-grp" id="(g-[a-z]+)"', tpl)
    assert ids == [g for g, _ in build.SG_GROUPS]


def test_plain_strips_tags():
    assert build._plain('농가 <small>단위</small>\n 소득') == "농가 단위 소득"
