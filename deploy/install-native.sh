#!/usr/bin/env bash
# Build the patched Native source as the service user. Never replace an existing installation implicitly.
set -euo pipefail
[[ $EUID == 0 ]]
root=/srv/sibot
src=$root/work/wechat-source/agent-wechat-2.3.2
if [[ -e "$root/bin/wx" ]]; then
    echo 'Existing wx binary. Use the feedback publisher for updates.' >&2
    exit 1
fi
apt-get install -y build-essential pkg-config libssl-dev curl git unzip ca-certificates
install -d -m 0750 -o sibot -g sibot "$root/work/wechat-source" "$root/cache/wechat-native-cargo"
runuser -u sibot -- env HOME="$root/home" SRC="$src" bash -s <<'BUILD'
set -euo pipefail
temp=$(mktemp -d "$HOME/native-install.XXXXXXXX")
trap 'rm -r -- "$temp"' EXIT
cd "$temp"
if [[ ! -d "$SRC" ]]; then
    curl --fail --location --retry 3 --output native.zip https://codeload.github.com/thisnick/agent-wechat/zip/refs/tags/v2.3.2
    echo '056d78fe6fdac7dedbe10963bf06694e983636abc0b174c16f35ac9eec9db982  native.zip' | sha256sum --check
    unzip -q native.zip
    git -C agent-wechat-2.3.2 apply --check /srv/sibot/deploy/native/si-native.patch
    git -C agent-wechat-2.3.2 apply --whitespace=nowarn /srv/sibot/deploy/native/si-native.patch
    mv agent-wechat-2.3.2 "$SRC"
fi
# On a retry, make sure the source contains all of the project changes.
git -C "$SRC" apply --reverse --check /srv/sibot/deploy/native/si-native.patch
if [[ ! -x "$HOME/.cargo/bin/rustup" ]]; then
    curl --proto '=https' --tlsv1.2 -fsS https://sh.rustup.rs -o rustup-init.sh
    sh rustup-init.sh -y --profile minimal --default-toolchain none --no-modify-path
fi
export PATH="$HOME/.cargo/bin:$PATH"
rustup toolchain install 1.93.1 --profile minimal
cd "$SRC/packages/native-client-rust"
export CARGO_TARGET_DIR=/srv/sibot/cache/wechat-native-cargo CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
cargo +1.93.1 build --locked -j 2 -p wx-native --bin wx
BUILD
install -m 0755 "$root/cache/wechat-native-cargo/debug/wx" "$root/bin/wx"
"$root/bin/wx" --version
systemctl enable --now sibot-wechat.service
for attempt in {1..30}; do
    [[ -s "$root/state/wechat/api-token" ]] && break
    sleep 1
done
[[ -s "$root/state/wechat/api-token" ]]
if [[ ! -e "$root/state/secrets/wechat-api" ]]; then
    install -m 0600 -o sibot -g sibot "$root/state/wechat/api-token" "$root/state/secrets/wechat-api"
fi
