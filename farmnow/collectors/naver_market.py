"""네이버 증권 시세 어댑터: 국내·해외 농업 관련 주식, 국내·해외 농산물 ETF, 국제 농산물 선물, 원/달러 환율.
종목마다 최근 일별 시세를 한 번씩 받아 최신가·등락·10일 흐름을 만든다. 설정(config/sources.yml)의 목록만 조회한다."""
from __future__ import annotations

import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
KR = "https://m.stock.naver.com/api/stock/{code}/price"
WORLD = "https://api.stock.naver.com/stock/{code}/price"
CMDT = "https://m.stock.naver.com/front-api/marketIndex/prices"
MAJORS = "https://m.stock.naver.com/front-api/marketIndex/majors"
UNITS = {"USc/BSH": "센트/부셸", "USD/TONS": "달러/숏톤", "USc/LBS": "센트/파운드", "USD/CWT": "달러/100파운드",
         "USD/TONNE": "달러/톤", "USD": "달러", "KRW": "원"}
PAUSE = 0.15


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _get(url: str, **params):
    r = requests.get(url, params=params, headers=UA, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    time.sleep(PAUSE)
    return r.json()


def _row(hist: list[dict], meta: dict) -> dict | None:
    hist = [h for h in hist if _num(h.get("closePrice")) is not None]
    if not hist:
        return None
    last = hist[0]                                   # 최신이 앞
    price = _num(last["closePrice"])
    chg = _num(last.get("compareToPreviousClosePrice"))
    if chg is None:
        chg = _num(last.get("fluctuations")) or 0.0
    kind = ((last.get("compareToPreviousPrice") or last.get("fluctuationsType") or {}).get("name") or "")
    if kind == "FALLING" and chg > 0:                # 국내 시세는 하락폭을 부호 없이 준다
        chg = -chg
    return {**meta, "price": price, "chg": chg, "pct": _num(last.get("fluctuationsRatio")) or 0.0,
            "volume": _num(last.get("accumulatedTradingVolume")),
            "day": str(last.get("localTradedAt", ""))[:10],
            "spark": [_num(h["closePrice"]) for h in reversed(hist[:10])],
            "sdays": [str(h.get("localTradedAt", ""))[5:10].replace("-", ".") for h in reversed(hist[:10])]}


def collect(src: dict) -> tuple[list, dict]:
    out = {"groups": src.get("groups", {}), "sections": {}}
    errors, total = [], 0
    for sec in ("kr_stocks", "world_stocks", "kr_etfs", "world_etfs", "commodities"):
        rows = []
        for s in src.get(sec, []):
            total += 1
            meta = {"code": s["code"], "name": s["name"], "group": s.get("group", ""),
                    "cur": s.get("cur") or ("USD" if sec.startswith("world") else "KRW")}
            try:
                if sec.startswith("kr"):
                    hist = _get(KR.format(code=s["code"]), pageSize=10, page=1)
                elif sec.startswith("world"):
                    hist = _get(WORLD.format(code=s["code"]), page=1, pageSize=10)
                else:
                    j = _get(CMDT, category="agricultural", reutersCode=s["code"], page=1, pageSize=10)
                    hist = j.get("result") or []
                    meta.update(cur="USD", unit=UNITS.get(s.get("unit", ""), s.get("unit", "")))
                one = _row(hist if isinstance(hist, list) else [], meta)
                if one:
                    rows.append(one)
                else:
                    errors.append(s["code"])
            except (requests.RequestException, RuntimeError, ValueError) as e:
                errors.append(f"{s['code']}({e})")
        out["sections"][sec] = rows
    try:                                             # 원/달러: 최근 10일 고시 환율
        j = _get(CMDT, category="exchange", reutersCode="FX_USDKRW", page=1, pageSize=10)
        fx = _row(j.get("result") or [], {"code": "FX_USDKRW", "name": "원/달러 환율", "group": "환율", "cur": "KRW", "unit": "원"})
        if fx:
            out["usdkrw"] = fx
    except (requests.RequestException, RuntimeError, ValueError):
        pass
    got = sum(len(v) for v in out["sections"].values())
    if got == 0:
        raise RuntimeError(f"시세 0건 (실패 {len(errors)})")
    out["missing"] = errors
    out["day"] = max(r["day"] for v in out["sections"].values() for r in v)
    from ..util import now_kst
    out["details"] = refresh_details(src, now_kst())
    return [], out


# ── 종목 상세(상세 페이지용): 1년 일별 시세·투자 지표·매매 동향·뉴스. 6시간마다, 한 번에 일부만 갱신 ──
import json
from datetime import datetime, timedelta
from pathlib import Path

DETAIL_DIR = Path(__file__).resolve().parents[2] / "data" / "market"
DETAIL_TTL = timedelta(hours=6)
DETAIL_BUDGET = 30                      # 한 번 실행에 새로 받는 종목 수 상한
KR_INT = "https://m.stock.naver.com/api/stock/{code}/integration"
KR_CHART = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
KR_NEWS = "https://m.stock.naver.com/api/news/stock/{code}"
W_BASIC = "https://api.stock.naver.com/stock/{code}/basic"
W_INT = "https://api.stock.naver.com/stock/{code}/integration"
W_CHART = "https://api.stock.naver.com/chart/foreign/item/{code}/day"
W_NEWS = "https://api.stock.naver.com/news/worldStock/{code}"
CM_DETAIL = "https://m.stock.naver.com/front-api/marketIndex/productDetail"


def safe(code: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in code)


def _infos(rows) -> list[dict]:
    return [{"k": t.get("key"), "v": t.get("value")} for t in rows or []
            if t.get("value") not in (None, "", "-", "N/A") and t.get("key")]


def _chart(rows) -> list[dict]:
    out = []
    for r in rows or []:
        d = str(r.get("localDate", ""))
        if len(d) == 8 and r.get("closePrice") is not None:
            out.append({"d": f"{d[:4]}-{d[4:6]}-{d[6:]}", "c": r["closePrice"], "o": r.get("openPrice"),
                        "h": r.get("highPrice"), "l": r.get("lowPrice"), "v": r.get("accumulatedTradingVolume")})
    return out


def _span(now: datetime) -> dict:
    return {"startDateTime": (now - timedelta(days=372)).strftime("%Y%m%d0000"), "endDateTime": now.strftime("%Y%m%d2359")}


def fetch_detail(sec: str, s: dict, now: datetime) -> dict:
    code = s["code"]
    d = {"code": code, "name": s["name"], "group": s.get("group", ""), "sec": sec, "at": now.isoformat()}
    if sec.startswith("kr"):
        j = _get(KR_INT.format(code=code))
        d["infos"] = _infos(j.get("totalInfos"))
        d["trend"] = [{"d": f"{t['bizdate'][4:6]}.{t['bizdate'][6:]}", "close": t.get("closePrice"),
                       "chg": t.get("compareToPreviousClosePrice"), "dir": (t.get("compareToPreviousPrice") or {}).get("name"),
                       "frgn": t.get("foreignerPureBuyQuant"), "org": t.get("organPureBuyQuant"),
                       "ind": t.get("individualPureBuyQuant"), "hold": t.get("foreignerHoldRatio")}
                      for t in (j.get("dealTrendInfos") or []) if t.get("bizdate")]
        d["market"] = (j.get("stockExchangeType") or {}).get("nameKor")
        d["url"] = f"https://m.stock.naver.com/domestic/stock/{code}/total"
        d["hist"] = _chart(_get(KR_CHART.format(code=code), **_span(now)))
        news = []
        for grp in _get(KR_NEWS.format(code=code), pageSize=6, page=1) or []:
            for n in grp.get("items") or []:
                news.append({"t": n.get("titleFull") or n.get("title"), "src": n.get("officeName"),
                             "dt": f"{n.get('datetime', '')[4:6]}.{n.get('datetime', '')[6:8]}", "u": n.get("mobileNewsUrl")})
        d["news"] = news[:6]
    elif sec.startswith("world"):
        b = _get(W_BASIC.format(code=code))
        d["infos"] = _infos(b.get("stockItemTotalInfos"))
        d["cur"] = (b.get("currencyType") or {}).get("code") or "USD"
        d["market"] = b.get("stockExchangeName")
        d["url"] = b.get("endUrl") or f"https://m.stock.naver.com/worldstock/stock/{code}"
        try:
            i = _get(W_INT.format(code=code))
            summ = (i.get("summaries") or {}).get("summary") or ""
            d["summary"] = summ[:420] + ("…" if len(summ) > 420 else "")
            c = i.get("consensusInfo") or {}
            if c.get("priceTargetMean"):
                d["target"] = {"mean": c.get("priceTargetMean"), "high": c.get("priceTargetHigh"), "low": c.get("priceTargetLow"),
                               "rec": c.get("recommMean"), "at": c.get("createDate")}
        except (requests.RequestException, RuntimeError, ValueError):
            pass
        d["hist"] = _chart(_get(W_CHART.format(code=code), **_span(now)))
        try:
            d["news"] = [{"t": n.get("tit"), "src": n.get("ohnm"), "dt": f"{str(n.get('dt', ''))[4:6]}.{str(n.get('dt', ''))[6:8]}", "u": d["url"]}
                         for n in (_get(W_NEWS.format(code=code), pageSize=6, page=1) or [])][:6]
        except (requests.RequestException, RuntimeError, ValueError):
            d["news"] = []
    else:
        r = (_get(CM_DETAIL, category="agricultural", reutersCode=code).get("result") or {})
        d["market"] = (r.get("stockExchangeType") or {}).get("nameKor")
        d["month"] = r.get("month")
        d["unit"] = UNITS.get(r.get("unit", ""), r.get("unit"))
        d["url"] = r.get("endUrl")
        rows = []
        for page in range(1, 6):                       # 60일씩 5쪽 ≈ 1년
            got = _get(CMDT, category="agricultural", reutersCode=code, page=page, pageSize=60).get("result") or []
            rows += got
            if len(got) < 60:
                break
        hist = [{"d": str(h["localTradedAt"])[:10], "c": _num(h["closePrice"]), "o": _num(h.get("openPrice")),
                 "h": _num(h.get("highPrice")), "l": _num(h.get("lowPrice")), "v": None} for h in rows if _num(h.get("closePrice"))]
        d["hist"] = sorted({h["d"]: h for h in hist}.values(), key=lambda h: h["d"])
    return d


def refresh_details(src: dict, now: datetime, budget: int = DETAIL_BUDGET) -> dict:
    """캐시가 오래된 종목부터 budget개만 새로 받는다. 실패해도 목록 시세 수집에는 영향이 없다."""
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    todo = []
    for sec in ("kr_stocks", "world_stocks", "kr_etfs", "world_etfs", "commodities"):
        for s in src.get(sec, []):
            p = DETAIL_DIR / f"{safe(s['code'])}.json"
            at = None
            if p.exists():
                try:
                    at = datetime.fromisoformat(json.loads(p.read_text(encoding="utf-8")).get("at"))
                except (ValueError, TypeError):
                    at = None
            if at is None or now - at >= DETAIL_TTL:
                todo.append((at or datetime.min.replace(tzinfo=now.tzinfo), sec, s, p))
    todo.sort(key=lambda t: t[0])
    done, fail = 0, []
    for _, sec, s, p in todo[:budget]:
        try:
            p.write_text(json.dumps(fetch_detail(sec, s, now), ensure_ascii=False), encoding="utf-8")
            done += 1
        except (requests.RequestException, RuntimeError, ValueError, KeyError) as e:
            fail.append(f"{s['code']}({type(e).__name__})")
    return {"refreshed": done, "pending": max(0, len(todo) - budget), "failed": fail}
