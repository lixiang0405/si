#!/usr/bin/env bash
# Optional editable reference checkouts for feedback work on the two upstream projects.
set -euo pipefail
[[ $EUID == 0 ]]
work=/srv/sibot/work
tmp=$(mktemp -d "$work/.upstreams.XXXXXXXX")
trap 'rm -r -- "$tmp"' EXIT
if [[ ! -e "$work/astrbot-source" ]]; then
    curl -fL --retry 3 -o "$tmp/astrbot.tar.gz" https://files.pythonhosted.org/packages/56/03/9dcc90ad10838cbe39ea143ae61daafccc14ff075d367acf7d3706e96c97/astrbot-4.29.4.tar.gz
    (cd "$tmp" && echo 'a5f67c86998028fcaee7c51c8d3c124e15a4ff7dc0c5d27a8f5e441a61a5b698  astrbot.tar.gz' | sha256sum -c -)
    tar --no-same-owner -xf "$tmp/astrbot.tar.gz" -C "$tmp"
    chown -R sibot:sibot "$tmp/astrbot-4.29.4"
    mv "$tmp/astrbot-4.29.4" "$work/astrbot-source"
fi
if [[ ! -e "$work/napcat-source" ]]; then
    rev=9335843246e3b7a17944861f3ac8731a259732c2
    git -C "$tmp" init -q napcat
    git -C "$tmp/napcat" fetch --depth 1 https://github.com/XingYunStar/NapCatQQ.git "$rev"
    [[ $(git -C "$tmp/napcat" rev-parse FETCH_HEAD) == "$rev" ]]
    git -C "$tmp/napcat" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    chown -R sibot:sibot "$tmp/napcat"
    mv "$tmp/napcat" "$work/napcat-source"
fi
