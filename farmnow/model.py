"""NewsItem 스키마와 게시 게이트."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime

# 자체 생성 문장(template)에만 적용하는 금칙어. 원문 인용(title_only·excerpt)에는 적용하지 않는다.
BANNED = ["사야", "팔아", "폭등", "대박", "전망", "유리", "불리", "급등 예상", "목표가"]
PII_RE = re.compile(r"(?<![\d-])0\d{1,2}[-.)\s]?\d{3,4}[-.\s]?\d{4}(?![\d-])|[\w.+-]+@[\w-]+\.[\w.-]+")
OPEN_LICENSES = ("kogl1", "kogl2", "no_limit")   # 인용·발췌가 허용되는 라이선스


@dataclass
class NewsItem:
    source_id: str
    source_name: str
    org: str
    url: str
    headline: str
    event_time: datetime | None          # 원출처 발표·조사·등록 시각
    time_precision: str                  # 'minute' | 'date'
    fetched_at: datetime
    category: str
    label: str                           # [발표][공고][입법예고][특보][급변] 등 사실형 라벨
    license: str
    body: str = ""
    body_mode: str = "title_only"        # template | excerpt | title_only
    generator: str = "rule"              # rule | template
    severity: str = "info"               # info | advisory | warning | alert
    tags_items: list[str] = field(default_factory=list)
    tags_events: list[str] = field(default_factory=list)
    tags_regions: list[str] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    expires_at: datetime | None = None
    quality_flags: list[str] = field(default_factory=list)
    uid: str | None = None               # 원천 고유키(게시물 번호·통보문 번호 등). 있으면 id의 기준
    registered_at: datetime | None = None  # RSS 등록 시각(배포 시각과 다를 수 있음)
    time_source: str = "feed"            # feed | page | api | survey
    extra: dict = field(default_factory=dict)  # 담당부서·첨부 수 등 부가 메타

    @property
    def id(self) -> str:
        key = f"{self.source_id}|{self.uid}" if self.uid else f"{self.source_id}|{self.url}|{self.headline}"
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    @property
    def lane(self) -> str:
        return "live" if self.time_precision == "minute" else "registry"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["lane"] = self.lane
        for k in ("event_time", "fetched_at", "expires_at", "registered_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        names = {f.name for f in fields(cls)}
        clean = {k: v for k, v in d.items() if k in names}
        for k in ("event_time", "fetched_at", "expires_at", "registered_at"):
            v = clean.get(k)
            clean[k] = datetime.fromisoformat(v) if isinstance(v, str) else v
        return cls(**clean)


def gate(item: NewsItem) -> tuple[bool, str]:
    """게시 게이트. (통과 여부, 사유). 본문 문제는 항목 거부가 아니라 본문 강등(title_only)으로 처리한다."""
    if not item.url or not item.source_name or not item.event_time:
        return False, "missing_required"
    if PII_RE.search(item.headline):
        return False, "pii_in_headline"
    if item.body:
        if item.generator != "template" and item.license not in OPEN_LICENSES:
            item.body, item.body_mode = "", "title_only"
            item.quality_flags.append("license_downgraded")
        elif PII_RE.search(item.body):
            item.body, item.body_mode = "", "title_only"
            item.quality_flags.append("pii_dropped")
    if item.generator == "template":
        text = item.headline + " " + item.body
        for w in BANNED:
            if w in text:
                return False, f"banned_word:{w}"
    if item.time_precision == "date":
        item.quality_flags.append("date_only")
    if item.license == "unknown":
        item.quality_flags.append("license_unknown")
    item.quality_flags = list(dict.fromkeys(item.quality_flags))
    return True, "ok"
