#!/usr/bin/env bash
set -euo pipefail

[[ $EUID == 0 && $# == 3 && $3 =~ ^[1-9][0-9]{4,17}$ ]] || { echo 'Usage: sudo bash install-qq.sh LINUXQQ.deb NapCat.Shell.zip BOT_QQ' >&2; exit 2; }
account=$3
qq_package=$(realpath -- "$1")
napcat_package=$(realpath -- "$2")
root=/srv/siqq
bundle="$root/qq-linux"
[[ $(dpkg-deb --field "$qq_package" Architecture) == amd64 ]]
[[ $(dpkg-deb --field "$qq_package" Package) == linuxqq ]]
[[ $(dpkg-deb --field "$qq_package" Version) == 3.2.34-53644 ]]
printf '436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e  %s\n' "$qq_package" | sha256sum --check --status
printf '02e5ff0f79916c1e04c81c1b4590056406fb4293f1f1300c955e71fd08b2829d  %s\n' "$napcat_package" | sha256sum --check --status
if ! id siqq >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$root/home" --no-create-home --shell /usr/sbin/nologin siqq
fi
install -d -m 0755 "$root" "$bundle"
install -d -m 0700 -o siqq -g siqq "$root/home" "$root/config" "$root/logs" "$root/cache" "$root/plugins"
app="$bundle/opt/QQ/resources/app"
if [[ ! -f "$bundle/opt/QQ/resources/app/loadSiNapCat.cjs" ]]; then
    dpkg-deb -x "$qq_package" "$bundle"
    install -m 0644 "$app/package.json" "$app/package.original.json"
    python3 - "$app" "$napcat_package" <<'PY'
import json
import sys
import zipfile
from pathlib import Path

app, archive = map(Path, sys.argv[1:])
with zipfile.ZipFile(archive) as release:
    for name in release.namelist():
        if not name.startswith('config/'):
            release.extract(name, app / 'napcat')
package = app / 'package.json'
settings = json.loads(package.read_text(encoding='utf-8'))
settings['main'] = './loadSiNapCat.cjs'
package.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
    cat > "$app/loadSiNapCat.cjs" <<'JS'
const { pathToFileURL } = require('node:url');
const path = require('node:path');
import(pathToFileURL(path.join(__dirname, 'napcat', 'napcat.mjs')).href);
JS
fi
for name in config logs cache plugins; do
    if [[ ! -e "$app/napcat/$name" ]]; then
        ln -s "$root/$name" "$app/napcat/$name"
    fi
done
if [[ ! -f "$root/config/webui.json" ]]; then
    install -m 0600 -o siqq -g siqq /srv/sibot/state/napcat-config/webui.json "$root/config/webui.json"
fi
for file in onebot11.json "onebot11_${account}.json"; do
    if [[ ! -e "$root/config/$file" ]]; then
        install -m 0600 -o siqq -g siqq /srv/sibot/state/napcat-config/onebot11.json "$root/config/$file"
    fi
done
cat > /etc/systemd/system/sibot-napcat.service <<'UNIT'
[Unit]
Description=Si QQ NapCat
Wants=network-online.target
After=network-online.target sibot-astrbot.service

[Service]
User=siqq
Group=siqq
WorkingDirectory=/srv/siqq/home
Environment=HOME=/srv/siqq/home
Environment=TZ=Asia/Shanghai
UMask=0077
ExecStart=/usr/bin/xvfb-run -a /srv/siqq/qq-linux/opt/QQ/qq --no-sandbox --disable-gpu
Restart=on-failure
RestartSec=8s
TimeoutStopSec=20s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/siqq/home /srv/siqq/config /srv/siqq/logs /srv/siqq/cache /srv/siqq/plugins
ProtectKernelTunables=true
ProtectKernelModules=true
RestrictSUIDSGID=true
LimitCORE=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable sibot-napcat.service
systemctl restart sibot-napcat.service
