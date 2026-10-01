#!/usr/bin/env bash
# 팜나우 갱신: 공공데이터 수집 → site/ 다시 만들기. cron이 이 파일만 실행한다(sudo 불필요).
#   서버 시간대가 UTC일 때 30분마다:  */30 * * * * /srv/farmnow/deploy/update.sh
# 기록: logs/update.log (1MB 넘으면 하나만 남기고 넘김)
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export TZ=Asia/Seoul PYTHONIOENCODING=utf-8 PYTHONPATH="$PWD/vendor${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p logs data

if [ -f logs/update.log ] && [ "$(stat -c %s logs/update.log)" -gt 1048576 ]; then
  mv -f logs/update.log logs/update.log.1
fi

# 앞선 실행이 아직 돌고 있으면 건너뛴다
exec 9>logs/.update.lock
if ! flock -n 9; then
  echo "$(date '+%F %T %Z') 앞선 갱신이 아직 실행 중이라 건너뜀" >> logs/update.log
  exit 0
fi

{
  echo "=== $(date '+%F %T %Z') 시작"
  if python3 -m farmnow.pipeline; then
    echo "=== $(date '+%F %T %Z') 끝"
  else
    echo "!!! 종료 코드 $? — 이전 화면이 그대로 남아 있다"
    exit 1
  fi
} >> logs/update.log 2>&1
