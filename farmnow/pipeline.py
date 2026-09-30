"""수집 → 게이트 → 규칙 → data/*.json 병합 → 정적 빌드."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .collectors import kamis, kma_warn, mafra_rss, nongsaro_list
from .model import NewsItem, gate
from .rules import tag
from .util import now_kst
from .build import build_site

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GRACE_DAYS = 7      # 적용 기간이 끝난 문서(병해충·주간농사정보)를 다음 호가 나올 때까지 남겨 두는 기간


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """저장소 밖 키 파일(.env, gitignore 대상). CI에서는 masked 변수를 쓰므로 파일이 없어도 된다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _read(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return default
    return default


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def run(build: bool = True) -> int:
    cfg = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))
    dicts = yaml.safe_load((ROOT / "config" / "dicts.yml").read_text(encoding="utf-8"))
    now = now_kst()
    keep_days = cfg["site"]["keep_days"]
    DATA.mkdir(exist_ok=True)
    store: dict[str, dict] = {}
    for d in _read(DATA / "items.json", []):
        d["id"] = NewsItem.from_dict(d).id      # 저장된 id 대신 현재 규칙으로 다시 계산(규칙이 바뀌어도 이어짐)
        store[d["id"]] = d
    prev_state = _read(DATA / "status.json", {})
    prev_status = prev_state.get("sources", {})

    status: dict[str, dict] = {}
    tickers: dict = {}
    warn = None
    fresh: list[NewsItem] = []
    for src in cfg["sources"]:
        st = {"name": src["name"], "at": now.isoformat(), "license": src.get("license")}
        try:
            if src["adapter"] == "mafra_rss":
                got = mafra_rss.collect(src, store, keep_days)
            elif src["adapter"] == "kamis":
                got, tickers = kamis.collect(src, cfg["site"]["surge_threshold_pct"])
            elif src["adapter"] == "nongsaro_list":
                got = nongsaro_list.collect(src)
            elif src["adapter"] == "kma_warn":
                got, warn = kma_warn.collect(src)
            else:
                raise RuntimeError(f"unknown adapter {src['adapter']}")
            st.update(ok=True, count=len(got), last_ok=now.isoformat())
            fresh.extend(got)
        except Exception as e:  # 한 신호원의 실패가 빌드를 막지 않는다
            no_key = str(e) == "no_api_key"
            st.update(ok=False, skipped=no_key, error="키 없음" if no_key else f"{type(e).__name__}: {e}"[:120],
                      last_ok=prev_status.get(src["id"], {}).get("last_ok"))
            print(f"[warn] {src['id']}: {st['error']}", file=sys.stderr)
        status[src["id"]] = st

    tried = [s for s in status.values() if not s.get("skipped")]
    if tried and not any(s["ok"] for s in tried):
        print("[error] 모든 신호원 수집 실패 — 이전 사이트를 유지하기 위해 빌드를 중단합니다.", file=sys.stderr)
        return 2

    rejected = []
    for it in fresh:
        ok, why = gate(it)
        if not ok:
            rejected.append((it.headline, why))
            continue
        tag(it, dicts)
        d = it.to_dict()
        prev = store.get(d["id"])
        if prev:
            d["fetched_at"] = prev["fetched_at"]  # 최초 수집 시각 유지
        store[d["id"]] = d

    keep_after = now - timedelta(days=keep_days)

    def alive(d: dict) -> bool:
        if datetime.fromisoformat(d["event_time"]) >= keep_after:
            return True
        return bool(d.get("expires_at")) and datetime.fromisoformat(d["expires_at"]) >= now - timedelta(days=GRACE_DAYS)

    kept = sorted((d for d in store.values() if alive(d)), key=lambda d: d["event_time"], reverse=True)
    published = Counter(d["source_id"] for d in kept)
    for sid, st in status.items():
        st["published"] = published.get(sid, 0)

    if warn is None and prev_state.get("warn"):       # 이번 회차에 현황을 못 얻으면 이전 값을 'stale'로 유지
        warn = dict(prev_state["warn"], stale=True)
    if tickers:
        _write(DATA / "tickers.json", tickers)
    _write(DATA / "items.json", kept)
    _write(DATA / "status.json", {"built_at": now.isoformat(), "sources": status, "warn": warn,
                                  "rejected": Counter(w for _, w in rejected)})

    print(f"collected {len(fresh)}, rejected {len(rejected)}, kept {len(kept)}")
    for h, why in rejected:
        print(f"  reject[{why}] {h[:50]}")
    if build:
        build_site(cfg, kept, status, now, warn)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(run(build="--no-build" not in sys.argv))
