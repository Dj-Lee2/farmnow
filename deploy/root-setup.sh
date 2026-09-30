#!/usr/bin/env bash
# root로 한 번만 실행한다: /srv/farmnow 를 만들고 Caddy에 farmnow.tech 를 추가한다.
# 기존 사이트 설정은 건드리지 않고 Caddyfile 맨 뒤에 블록만 덧붙인다(백업 후, 검증 실패 시 되돌림).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root로 실행하세요"; exit 1; }
SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OWNER="${1:-${SUDO_USER:-www-data}}"   # 사이트 파일을 소유할 계정(첫 인자 또는 sudo를 부른 계정)
CF=/etc/caddy/Caddyfile

mkdir -p /srv/farmnow
cp -a "$SRC/." /srv/farmnow/
rm -f /srv/farmnow/src.tgz
chown -R "$OWNER:$OWNER" /srv/farmnow
chmod 755 /srv/farmnow          # 웹서버(caddy)가 site/ 를 읽을 수 있어야 한다
[ -f /srv/farmnow/.env ] && chmod 600 /srv/farmnow/.env
chmod +x /srv/farmnow/deploy/*.sh

if grep -q '^farmnow\.tech' "$CF"; then
  echo "Caddyfile에 farmnow.tech 가 이미 있습니다 — 건너뜀"
else
  BAK="$CF.bak-farmnow-$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$CF" "$BAK"
  { printf '\n'; cat /srv/farmnow/deploy/Caddyfile.farmnow; } >> "$CF"
  if caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy
    echo "Caddy 반영 완료 (백업: $BAK)"
  else
    cp "$BAK" "$CF"
    echo "Caddyfile 검증 실패 — 백업으로 되돌렸습니다"; exit 1
  fi
fi
sudo -u "$OWNER" /srv/farmnow/deploy/update.sh && echo "첫 갱신 완료: https://farmnow.tech"
