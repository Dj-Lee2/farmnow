"""KAMIS 중계: KAMIS가 해외 서버 IP를 막을 때, 국내 기기에서 받아 서버의 data/relay/로 보낸다.
  python3 deploy/kamis_relay.py <보낼 곳(scp 대상, 예: 사용자@서버:/srv/farmnow/data/relay/)>
환경변수 KAMIS_KEY·KAMIS_ID가 필요하다. 월별 평년 자료(season.json)는 하루 한 번만 다시 받는다."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "vendor")]
from farmnow.collectors import kamis, kamis_season  # noqa: E402


def main(dest: str) -> int:
    cfg = yaml.safe_load((ROOT / "config/sources.yml").read_text(encoding="utf-8"))
    src = dict(next(s for s in cfg["sources"] if s["id"] == "kamis_daily"), relay_dir=None)
    out = Path(tempfile.mkdtemp(prefix="kamis-relay-"))
    j = kamis.fetch_json(src)
    if j.get("error_code") != "000" or not j.get("price"):
        print(f"KAMIS 응답 이상: error_code={j.get('error_code')}", file=sys.stderr)
        return 1
    (out / "kamis_daily.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    files = [out / "kamis_daily.json"]
    cache = Path.home() / ".cache/farmnow-relay"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        before = (cache / "season.json").stat().st_mtime if (cache / "season.json").exists() else 0
        kamis_season.refresh(src.get("season_items") or [], cache, src)
        if (cache / "season.json").stat().st_mtime != before:
            files.append(cache / "season.json")
    except Exception as e:  # 평년 자료는 덧붙이는 정보
        print(f"season: {type(e).__name__}: {e}", file=sys.stderr)
    subprocess.run(["scp", "-q", *map(str, files), dest], check=True)
    print(f"sent {[f.name for f in files]} prices={len(j['price'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
