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


# 주소에서 .html 빼기: 사이트 안 링크(href·자바스크립트 문자열)의 'news.html' → 'news', 'index.html' → './'
# (서버가 /news 요청에 news.html을 내주고, 옛 .html 주소는 새 주소로 넘긴다)
_HTML_REF = re.compile(r"""((?:href|action)=["'](?:\./)?|["'](?:\./)?)([a-z0-9][\w-]*)\.html(?=[?#"'])""")


def clean_urls(text: str) -> str:
    def sub(m):
        if m.group(2) != "index":
            return m.group(1) + m.group(2)
        return m.group(1) if m.group(1).endswith("./") else m.group(1) + "./"
    return _HTML_REF.sub(sub, text).replace("+'.html", "+'")
