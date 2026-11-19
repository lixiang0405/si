#!/usr/bin/env bash
# Install the CLI, its matching Bubblewrap executable and supporting files as a versioned unit.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == x86_64 ]]
dir=/opt/codex/0.169.0-linux-x64
if [[ ! -x "$dir/bin/codex" ]]; then
    install -d -m 0755 /opt/codex
    temp=$(mktemp -d /opt/codex/.install.XXXXXXXX)
    trap 'rm -r -- "$temp"' EXIT
    cd "$temp"
    curl -fL --retry 3 -o codex.tgz https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-package-x86_64-unknown-linux-musl.tar.gz
    echo '3b8ed091149cc63a59046b9f094bcfa21cdb4dda15ede441ce369bcee4a71ba0  codex.tgz' | sha256sum -c -
    mkdir package
    tar --no-same-owner -xzf codex.tgz -C package
    binary=$(find package -type f -path '*/bin/codex' -print -quit)
    [[ -n $binary ]]
    packaged=$(dirname "$(dirname "$binary")")
    test -x "$packaged/codex-resources/bwrap"
    test ! -e "$dir"
    mv "$packaged" "$dir"
    chmod 0755 "$dir"
fi
test -x "$dir/codex-resources/bwrap"
ln -sfn "$dir/bin/codex" /srv/sibot/bin/codex
/srv/sibot/bin/codex --version
