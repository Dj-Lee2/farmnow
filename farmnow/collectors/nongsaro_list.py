"""농사로 게시판 목록(주간농사정보·병해충발생정보) 어댑터. 제목·등록일·적용 기간과 문서 바로보기 링크만 수집한다.
작성자 열은 저장하지 않는다."""
from __future__ import annotations

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ..model import NewsItem
from ..util import now_kst, KST

VIEW_URL = "https://www.nongsaro.go.kr/portal/contentsFileView.do?cntntsNo={no}&fileSeCode={se}&fileSn={sn}"
# (2026.9.1~9.30) / (2026. 9. 28.~10. 4.) / (2026.10.5.~10.11.)
_PERIOD = re.compile(r"\((\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*~\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*\)")
_FNC = re.compile(r"fncFile(?:View|Down)\(([^)]*)\)")


def _view_link(tr_html: str, fallback: str) -> tuple[str, str | None]:
    m = _FNC.search(tr_html)
    if not m:
        return fallback, None
    nums = re.findall(r"'(\d+)'", m.group(1))
    if len(nums) == 2:      # (cntntsNo, fileSeCode)
        return VIEW_URL.format(no=nums[0], se=nums[1], sn=1), nums[0]
    if len(nums) >= 3:      # (cntntsNo, fileSn, fileSeCode)
        return VIEW_URL.format(no=nums[0], se=nums[2], sn=nums[1]), nums[0]
    return fallback, None


def collect(src: dict, limit: int = 5) -> list[NewsItem]:
    r = requests.get(src["url"], timeout=30, headers={"User-Agent": "Mozilla/5.0 farmnow/0.2 (+https://farmnow.tech)"})
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    heads = [th.get_text(strip=True) for th in soup.select("table.tbl thead th")]
    i_title = heads.index("제목") if "제목" in heads else 1
    i_date = heads.index("등록일") if "등록일" in heads else 3
    fetched = now_kst()
    items: list[NewsItem] = []
    for tr in soup.select("table.tbl tbody tr")[:limit]:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) <= max(i_title, i_date):
            continue
        title = re.sub(r"제\s+(\d+)호", r"제\1호", tds[i_title])
        m = re.search(r"\d{4}-\d{2}-\d{2}", tds[i_date])
        if not m:
            continue
        day = datetime.strptime(m.group(0), "%Y-%m-%d").replace(tzinfo=KST)
        url, cntnts = _view_link(str(tr), src["url"])
        extra, expires = {}, None
        p = _PERIOD.search(title)
        if p:
            y, sm, sd, em, ed = map(int, p.groups())
            start = datetime(y, sm, sd, tzinfo=KST)
            expires = datetime(y + (1 if em < sm else 0), em, ed, 23, 59, tzinfo=KST)
            extra = {"start": start.isoformat(), "period": f"{sm}.{sd}~{em}.{ed}"}
        no = re.search(r"제\d+호", title)
        if no:
            extra["no"] = no.group(0)
        items.append(NewsItem(
            source_id=src["id"], source_name=src["name"], org=src["org"], url=url,
            headline=f"농진청, {title} 등록", event_time=day, time_precision="date", fetched_at=fetched,
            category=src["category"], label=src.get("label", "등록"), license=src.get("license", "unknown"),
            severity="advisory" if src["category"] == "pest" else "info", expires_at=expires,
            uid=cntnts or title, time_source="page", extra=extra,
        ))
    return items
