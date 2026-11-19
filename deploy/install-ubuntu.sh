#!/usr/bin/env bash
# Fresh, dedicated Ubuntu host. Install optional messaging transports separately.
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Usage: sudo bash deploy/install-ubuntu.sh play.example.org' >&2; exit 2; }
host=${1,,}
python3 - "$host" <<'PY'
import re, sys
host = sys.argv[1]
labels = host.split('.')
if len(host) > 253 or len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', s) for s in labels):
    raise SystemExit("Supply a DNS hostname; use the same name in your A record.")
PY
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == amd64 ]]
root=/srv/sibot
repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
marker=$root/.public-bootstrap
if [[ -e "$root" && ! -f "$marker" ]]; then
    echo "Existing installation at $root. This bootstrap installs on a fresh host." >&2
    exit 1
fi
if [[ -e "$marker" && $(cat "$marker") == complete ]]; then
    echo "Already installed. Use README.md for account setup and updates."
    exit 0
fi
install -d -m 0755 "$root"
touch "$marker"
apt-get update
apt-get install -y python3-venv ca-certificates curl git rsync unzip ffmpeg
id sibot >/dev/null 2>&1 || useradd --system --user-group --home-dir "$root/home" --no-create-home --shell /usr/sbin/nologin sibot
install -d -m 0755 "$root/bin" "$root/deploy" "$root/outputs" "$root/work"
install -d -m 0750 -o sibot -g sibot "$root/home" "$root/state" "$root/work/astrbot-instance"
install -d -m 0700 -o sibot -g sibot "$root/state/secrets" "$root/state/wechat"
install -d -m 2750 -o root -g sibot "$root/work/feedback-deployments"
install -d -m 0750 -o root -g sibot /etc/si-game-https
rsync -a --chown=root:root --chmod=D755,F644 "$repo/deploy/" "$root/deploy/"
install -m 0644 "$repo"/outputs/{Deploy-SiBotFeedback.py,Deploy-SiBotFeedbackLinux.py,Deploy-WeChatNativeLinux.py,SiBotHostDeploy.py} "$root/outputs/"
install -m 0755 "$repo/deploy/codex-proxy" "$root/bin/codex-proxy"
python3 -m venv "$root/venv"
"$root/venv/bin/pip" install -c "$root/deploy/ubuntu-py312-constraints.txt" 'astrbot==4.29.4' \
    -r "$repo/work/astrbot_plugin_qoder_neko/requirements.txt" -r "$repo/work/astrbot_plugin_wechat_native/requirements.txt"
for name in astrbot_plugin_qoder_neko astrbot_plugin_wechat_native si-game-room-source; do
    install -d -o sibot -g sibot -m 0750 "$root/work/$name"
    rsync -a --exclude=.git --exclude=__pycache__ --exclude=.runtime --exclude=.deployments --chown=sibot:sibot "$repo/work/$name/" "$root/work/$name/"
done
runuser -u sibot -- env HOME="$root/home" "$root/venv/bin/python" "$root/deploy/init-config.py"
plugins=$root/work/astrbot-instance/data/plugins
for name in astrbot_plugin_qoder_neko astrbot_plugin_wechat_native; do
    rsync -a --chown=sibot:sibot "$root/work/$name/" "$plugins/$name/"
done
install -d -o sibot -g sibot -m 0750 "$root/work/si-game-room"
rsync -a --exclude=test_\*.py --exclude=deploy.py --chown=sibot:sibot "$root/work/si-game-room-source/" "$root/work/si-game-room/"
if [[ ! -x "$root/bin/caddy-game-https" ]]; then
    scratch=$(mktemp -d)
    (cd "$scratch" && apt-get download caddy && dpkg-deb -x ./caddy_*.deb extract)
    install -m 0755 "$scratch/extract/usr/bin/caddy" "$root/bin/caddy-game-https"
    rm -r -- "$scratch"
fi
printf 'SI_GAME_HTTPS_HOST=%s\n' "$host" > /etc/si-game-https/public-host.env
install -m 0644 "$repo/deploy/Caddyfile" /etc/si-game-https/Caddyfile
env SI_GAME_HTTPS_HOST="$host" "$root/bin/caddy-game-https" validate --config /etc/si-game-https/Caddyfile --adapter caddyfile
install -m 0644 "$repo"/deploy/systemd/sibot-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sibot-game.service sibot-game-https.service sibot-astrbot.service
printf 'complete\n' > "$marker"
echo 'Base service installed. Follow README.md to set Qoder credentials, log in to QQ/WeChat and enable feedback.'
