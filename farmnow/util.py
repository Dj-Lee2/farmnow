import re
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


_HANJA = "㐀-䶿一-鿿豈-﫿"
_HANJA_PAREN = re.compile(rf"\s*[(（][{_HANJA}·ㆍ,\s]+[)）]")
_HANJA_ANY = re.compile(rf"[{_HANJA}]+")


def hangul_only(text: str) -> str:
    """화면에는 한글만 쓴다: '소(牛)'처럼 괄호로 덧붙인 한자는 괄호째 지우고, '전일比'는 '전일 대비'로, 남은 한자는 뺀다."""
    text = _HANJA_PAREN.sub("", text).replace("比", " 대비")
    return _HANJA_ANY.sub("", text)
