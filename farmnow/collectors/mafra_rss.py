"""농식품부 RSS 어댑터.
RSS에는 title·link·pubDate(등록 시각)·author만 있다. 새 항목만 원문 1회 조회해
(1) 실제 배포일시 (2) 게시물별 공공누리 유형 (3) 부처가 쓴 첫 문장 발췌(공공누리 1·2유형만)를 얻는다.
author(담당자명)·작성자 필드는 저장하지 않는다. 원문을 확인하지 못한 새 항목은 게시하지 않고 다음 실행에서 다시 시도한다."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup

from ..model import NewsItem, OPEN_LICENSES
from ..util import now_kst, KST

UA = {"User-Agent": "farmnow/0.3 (+https://farmnow.tech)"}
LABEL_BOARDS = ("subsidy_notice", "legislation")     # 제목으로 라벨을 다시 정하는 게시판
_TITLE_LABEL = [
    (re.compile(r"재행정예고"), "재행정예고"),
    (re.compile(r"행정예고"), "행정예고"),
    (re.compile(r"입법예고"), "입법예고"),
    (re.compile(r"공모|공고|모집"), "공고"),
]
_DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})(?:\s+(\d{2}):(\d{2}))?")
_SENT_RE = re.compile(r".+?(?:다|요|음|임|함)\.(?=\s|$|[가-힣“\"‘'(])")
_CONTACT_RE = re.compile(r"담당자|문의|연락처|주무관|사무관|서기관|☎|Tel|\d{2,4}[-.)\s]\d{3,4}[-.\s]\d{4}|@")
_HEADING_RE = re.compile(r"^[<〈《\[□○◇■▶※*\-\d]")
_OPEN, _CLOSE = "‘“「『", "’”」』"
MAX_LEN = 200


def _parse_pubdate(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _balanced(s: str) -> bool:
    return sum(s.count(c) for c in _OPEN) == sum(s.count(c) for c in _CLOSE) and s.count('"') % 2 == 0


def excerpt(view) -> str:
    """본문에서 첫 완결 문장 하나(짧으면 둘). 제목줄·목록줄·담당자 줄은 건너뛰고, 문장이 끊기거나
    따옴표가 닫히지 않거나 200자를 넘으면 발췌하지 않는다."""
    if view is None:
        return ""
    for p in view.find_all("p"):
        for br in p.find_all("br"):
            br.replace_with("\n")
        for line in p.get_text().replace("\xa0", " ").split("\n"):
            t = re.sub(r"\s+", " ", line).strip()
            t = re.sub(r"\s+([,.)”’])", r"\1", t)
            if len(t) < 40 or _HEADING_RE.match(t) or _CONTACT_RE.search(t):
                continue
            sents = [m.group(0).strip() for m in _SENT_RE.finditer(t)]
            if not sents:
                return ""                      # 첫 본문 줄이 완결 문장이 아니면 발췌하지 않는다
            out, i = sents[0], 1
            while not _balanced(out) and i < len(sents):       # 인용문 안에서 끊기지 않게 이어 붙인다
                out, i = f"{out} {sents[i]}", i + 1
            if i < len(sents) and len(out) < 60 and len(out) + len(sents[i]) < MAX_LEN:
                out = f"{out} {sents[i]}"
            return out if _balanced(out) and len(out) <= MAX_LEN else ""
    return ""


def fetch_detail(url: str) -> dict:
    """원문에서 배포일시·라이선스·발췌를 얻는다."""
    r = requests.get(url.replace("http://", "https://"), timeout=10, headers=UA)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict = {}
    dd = soup.select_one("dd.date")
    if dd:
        m = _DATE_RE.search(dd.get_text(" ", strip=True))
        if m:
            y, mo, d, hh, mi = m.groups()
            out["date"] = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), tzinfo=KST)
            out["has_time"] = hh is not None
    a = soup.select_one(".kogl_wrap a[href*=licenseType]")
    if a:
        m = re.search(r"licenseType(\d)", a.get("href", ""))
        if m:
            out["license"] = f"kogl{m.group(1)}"
    out["excerpt"] = excerpt(soup.select_one(".view_contents"))
    return out


def _entries(src: dict, known_uids: set[str], cutoff: datetime) -> list:
    """새 글이 없는 쪽이 나올 때까지(최대 backfill_pages) RSS를 넘겨 읽는다."""
    out = []
    for page in range(1, int(src.get("backfill_pages", 5)) + 1):
        r = requests.get(src["url"], params={"page": page}, timeout=20, headers=UA)
        r.raise_for_status()
        got = feedparser.parse(r.content).entries
        if not got:
            break
        out.extend(got)
        fresh = 0
        for e in got:
            reg = _parse_pubdate(e.get("published", ""))
            m = re.search(r"/(\d+)/artclView", e.get("link", ""))
            if reg and reg >= cutoff and m and m.group(1) not in known_uids:
                fresh += 1
        if fresh == 0:
            break
    return out


def collect(src: dict, known: dict | None = None, keep_days: int = 14) -> list[NewsItem]:
    known = known or {}
    now = now_kst()
    cutoff = now - timedelta(days=keep_days)
    known_uids = {v.get("uid") for v in known.values() if v.get("source_id") == src["id"]}
    items: list[NewsItem] = []
    seen: set[str] = set()
    for e in _entries(src, known_uids, cutoff):
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        link = e.get("link", "").strip().replace("http://", "https://")
        reg = _parse_pubdate(e.get("published", "") or e.get("pubdate", ""))
        if not (title and link and reg) or reg < cutoff:
            continue
        m = re.search(r"/(\d+)/artclView", link)
        label = src.get("label", "발표")
        if src["category"] in LABEL_BOARDS:
            label = next((lb for rx, lb in _TITLE_LABEL if rx.search(title)), label)
        it = NewsItem(
            source_id=src["id"], source_name=src["name"], org=src["org"], url=link, headline=title,
            event_time=reg, time_precision="minute", fetched_at=now, category=src["category"], label=label,
            license=src.get("license", "unknown"), uid=m.group(1) if m else None, registered_at=reg,
        )
        if it.id in seen:
            continue
        seen.add(it.id)
        prev = known.get(it.id)
        if prev and prev.get("time_source") == "page":      # 이미 원문을 확인한 항목은 다시 조회하지 않는다
            it.event_time = datetime.fromisoformat(prev["event_time"])
            it.time_precision = prev["time_precision"]
            it.license = prev.get("license", it.license)
            it.body, it.body_mode = prev.get("body", ""), prev.get("body_mode", "title_only")
            it.time_source = "page"
        else:
            try:
                d = fetch_detail(link)
                time.sleep(0.3)
            except Exception:
                d = {}
            if not d.get("date"):
                continue                       # 배포일시를 확인하지 못했으면 게시하지 않고 다음 실행에서 재시도
            if d["has_time"]:
                it.event_time, it.time_precision = d["date"], "minute"
            elif d["date"].date() != reg.date():
                it.event_time, it.time_precision = d["date"], "date"
            it.time_source = "page"
            if d.get("license"):
                it.license = d["license"]
            # 설명자료는 첫 문단이 언론 보도 내용 인용이라 발췌하지 않는다
            if d.get("excerpt") and it.license in OPEN_LICENSES and src.get("excerpt", True):
                it.body, it.body_mode = d["excerpt"], "excerpt"
        if it.event_time > now + timedelta(minutes=5):      # 배포 예정 항목은 그 시각이 지난 뒤 게시
            continue
        items.append(it)
    return items
