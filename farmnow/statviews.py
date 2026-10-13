"""통계 탭의 품목별·지역별 보기.
같은 KOSIS 계열(data/stats.json)을 품목(사과·한우…)과 시도(9개 도) 기준으로 다시 모은다."""
from __future__ import annotations

import re

PROV = [("gg", "경기"), ("gw", "강원"), ("cb", "충북"), ("cn", "충남"), ("jb", "전북"),
        ("jn", "전남"), ("gb", "경북"), ("gn", "경남"), ("jj", "제주")]
PROV_NAME = dict(PROV)
_PROV_ALIAS = {"경기": "gg", "강원": "gw", "충북": "cb", "충청북도": "cb", "충남": "cn", "충청남도": "cn", "전북": "jb",
               "전라북도": "jb", "전남": "jn", "전라남도": "jn", "경북": "gb", "경상북도": "gb", "경남": "gn",
               "경상남도": "gn", "제주": "jj"}
# 타일 지도 위치(행, 열)
TILE = {"gg": (1, 2), "gw": (1, 3), "cn": (2, 1), "cb": (2, 2), "gb": (2, 3), "jb": (3, 1), "gn": (3, 3), "jn": (4, 1), "jj": (5, 1)}

# 품목: (쪽 이름, 표시 이름, 묶음, 제목에서 찾을 이름들, 시세 품목 이름 앞부분)
ITEMS = [
    ("rice", "쌀", "식량", ["쌀", "논벼"], "쌀/20kg"), ("soy", "콩", "식량", ["콩"], "콩/"),
    ("potato", "감자", "식량", ["감자"], "감자/"), ("sweetpotato", "고구마", "식량", ["고구마"], "고구마/"),
    ("cabbage", "배추", "채소", ["배추", "고랭지 배추"], "배추/"), ("radish", "무", "채소", ["무"], "무/"),
    ("pepper", "고추", "채소", ["고추", "건고추"], "건고추/"), ("garlic", "마늘", "채소", ["마늘"], "깐마늘(국산)/"),
    ("onion", "양파", "채소", ["양파"], "양파/"), ("greenonion", "대파", "채소", ["대파", "파"], "파/대파"),
    ("apple", "사과", "과수", ["사과"], "사과/"), ("pear", "배", "과수", ["배"], "배/"),
    ("citrus", "감귤", "과수", ["감귤"], "감귤/"), ("grape", "포도", "과수", ["포도"], "포도/"),
    ("peach", "복숭아", "과수", ["복숭아"], "복숭아/"), ("persimmon", "감", "과수", ["단감", "감", "떫은감"], "단감/"),
    ("hanwoo", "한우", "축산", ["한우", "한육우"], "소/"), ("dairy", "젖소·우유", "축산", ["젖소", "우유", "원유"], "우유/"),
    ("pig", "돼지", "축산", ["돼지", "비육돈"], "돼지/삼겹살"), ("chicken", "닭·계란", "축산", ["육계", "산란계", "계란", "닭"], "계란/"),
]

CAP = 40          # 칸마다 카드 상한(나머지는 통계 찾기로)
# 제목 낱말 → 칸(위에서부터 먼저 맞는 칸)
SECTIONS = [
    ("지역", None),
    ("가격", re.compile(r"물가|가격지수|수취가격|가격")),
    ("소비·가공", re.compile(r"외식|식품제조|가공|소비량|건강기능|전처리|한식|급식")),
    ("노동", re.compile(r"노동|기계화율|작업")),
    ("돈", re.compile(r"소득|경영비|생산비|비료비|농약비|노동비|임차료|순수익|총수입|비용|사료비|사육비|부가가치|평가액|자본")),
    ("투입", re.compile(r"비료|퇴비|농약|사료|약제")),
    ("생산·면적", re.compile(r"생산량|재배면적|면적|수량|사육|마릿수|농장|농가|출하|도축|번식|생산")),
]


def _rx(names: list[str]) -> re.Pattern:
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    return re.compile(rf"(?:^|[\s(·—,])(?:{alt})(?=$|[\s()·—,])")


def _prov_of(key: str, title: str) -> str | None:
    m = re.search(r"_(gg|gw|cb|cn|jb|jn|gb|gn|jj)$", key) or re.search(r"\((gg|gw|cb|cn|jb|jn|gb|gn|jj)\)", title)
    if m:
        return m.group(1)
    for name, code in _PROV_ALIAS.items():
        if re.search(rf"(?:^|[\s(—·]){name}(?=$|[\s)—·,])", title):
            return code
    return None


def _strip_prov(title: str) -> str:
    t = re.sub(r"\((?:gg|gw|cb|cn|jb|jn|gb|gn|jj)\)", "", title)
    for name in sorted(_PROV_ALIAS, key=len, reverse=True):
        t = re.sub(rf"(?:^|(?<=[\s(—·])){name}(?=$|[\s)—·,])", "", t)
    t = re.sub(r"도별|시도별|\s*—\s*$|^\s*—\s*", "", t)
    return re.sub(r"\s+", " ", t).strip(" ·—")


def _ok(s: dict) -> bool:
    v = [x for x in s.get("vals") or [] if x is not None]
    return len(v) >= 1 and s.get("unit") not in (None, "")


def item_view(kstat: dict, item: tuple) -> dict:
    """품목 한 개의 계열을 칸별로 모은다. 도별 계열은 같은 지표끼리 묶어 9개 도 비교로 만든다."""
    slug, label, grp, names, _ = item
    rx = _rx(names)
    secs: dict[str, list] = {name: [] for name, _ in SECTIONS}
    regional: dict[str, dict] = {}
    cities: dict[str, dict] = {}
    for key, s in kstat.items():
        t = s.get("title", "")
        if not rx.search(t) or not _ok(s):
            continue
        p = _prov_of(key, t)
        m = rx.search(t)
        pre = t[:m.start()].strip(" (")
        if not p and pre and " " not in pre and len(pre) <= 4 and not pre.startswith(("논", "밭", "노지", "시설", "고랭지", "임가", "농업법인")):
            rest = t[m.start():].strip()   # '고창 논벼 생산량' 같은 시군 계열
            cities.setdefault(rest, {})[pre] = (key, s)
            continue
        if p:
            base = _strip_prov(t)
            regional.setdefault(base, {"title": base, "unit": s.get("unit"), "rows": {}, "src": s})["rows"][p] = s
            continue
        for name, pat in SECTIONS[1:]:
            if pat.search(t):
                secs[name].append((key, s))
                break
        else:
            secs["생산·면적"].append((key, s))
    regions = []
    for base, r in regional.items():
        if len(r["rows"]) < 4:
            continue
        rows = sorted(((PROV_NAME[c], s) for c, s in r["rows"].items()), key=lambda x: -(x[1]["vals"][-1] or 0))
        regions.append({"title": base, "unit": r["unit"], "rows": rows, "src": r["src"]})
    regions.sort(key=lambda r: (-len(r["rows"]), r["title"]))
    for rest, rows in cities.items():
        if len(rows) >= 5:
            srt = sorted(((pre, ks[1]) for pre, ks in rows.items()), key=lambda x: -(x[1]["vals"][-1] or 0))[:12]
            regions.append({"title": f"시군별 {rest}", "unit": srt[0][1].get("unit"), "rows": srt, "src": srt[0][1]})
        else:
            secs["생산·면적"].extend(rows.values())
    def rank(r):
        t = r["title"]
        return (0 if re.search(r"생산량$|재배면적$|마릿수$", t) else 1 if re.search(r"소득|생산비|경영비", t) else 2,
                t.startswith("시군별"), not t.startswith(tuple(names)), -len(r["rows"]), t)
    regions.sort(key=rank)
    secs["지역"] = regions[:12]
    lead = tuple(names)
    for name in secs:
        if name != "지역":
            secs[name].sort(key=lambda kv: (not kv[1]["title"].startswith(lead), len(kv[1]["title"]), kv[1]["title"]))
    n = sum(len(v) for v in secs.values())
    for name in secs:
        secs[name] = secs[name][:CAP]
    kpi = []
    for wants in ((r"^\S+ 생산량$", r"생산량$"), (r"^\S+ 재배면적$", r"사육 마릿수$", r"재배면적$"),
                  (r"^\S+ 10a당 소득$", r"10a당 소득$", r"두당 순수익$", r"두당 소득$"), (r"수취가격$",)):
        hit = None
        for want in wants:
            hit = next(((k, s) for sec in ("생산·면적", "돈", "가격") for k, s in secs[sec]
                        if re.search(want, s["title"]) and len(s["vals"]) > 1), None)
            if hit:
                break
        if hit and hit not in kpi:
            kpi.append(hit)
    return {"slug": slug, "label": label, "group": grp, "sections": [(k, v) for k, v in secs.items() if v], "kpi": kpi, "n": n}


# 지역별 지도에 올리는 지표: (분류, 계열 묶음, 표시 이름, 큰 값이 '좋음'인지 여부는 따지지 않고 색은 진하기만)
REGION_IND = [
    ("사람", "fd", "농가 수"), ("사람", "flpr", "농지연금 수급자"), ("사람", "mcf_hh", "다문화 농가"),
    ("돈", "ir", "농가소득"), ("돈", "flr_rent", "㎡당 연간 임차료"), ("돈", "cbr", "농업법인 수"),
    ("땅·작물", "ad", "경지면적"), ("땅·작물", "rp", "쌀 생산량"), ("땅·작물", "ove_tot", "노지 채소 재배면적"),
    ("땅·작물", "sfr", "시설작물 재배면적"), ("땅·작물", "ecr", "친환경 인증 면적"),
    ("축산", "hsd", "한우 마릿수"), ("축산", "psd", "돼지 마릿수"), ("축산", "dkr", "육용오리 마릿수"),
    ("환경", "ghr_a", "농업 온실가스 배출량"), ("환경", "gwr_no3", "지하수 질산성질소"), ("환경", "lcv_gh", "면적 중 시설재배지 비율"),
]


def region_view(kstat: dict) -> dict:
    """지역 지도용 자료: 지표마다 9개 도의 최근 값·추이와 기준 시점."""
    inds = []
    for cat, fam, label in REGION_IND:
        rows = {c: kstat.get(f"{fam}_{c}") for c, _ in PROV}
        if sum(1 for s in rows.values() if s and s.get("vals")) < 8:
            continue
        any_s = next(s for s in rows.values() if s)
        yr = min(s["years"][-1] for s in rows.values() if s)
        vals = {}
        for c, s in rows.items():
            if not s or yr not in s["years"]:
                continue
            i = s["years"].index(yr)
            v = s["vals"][i]
            pv = s["vals"][i - 1] if i > 0 else None
            vals[c] = {"v": v, "p": round((v - pv) / abs(pv) * 100, 1) if pv else None,
                       "t": [x for x in s["vals"][max(0, i - 9):i + 1]]}
        inds.append({"id": fam, "cat": cat, "label": label, "unit": any_s.get("unit"), "year": yr,
                     "tbl": any_s.get("tbl"), "org": any_s.get("org"), "vals": vals})
    return {"provs": [{"c": c, "n": n, "r": TILE[c][0], "k": TILE[c][1]} for c, n in PROV], "inds": inds}
