"""Initialize a fresh installation while preserving existing passwords and operator settings."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


def save(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    os.replace(tmp.name, path)


def secret(path):
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    value = path.read_text(encoding="ascii").strip()
    if not value:
        raise ValueError(f"Empty credential file: {path}")
    return value


def initialize(root):
    os.umask(0o077)
    work = root / "work"
    inst = work / "astrbot-instance"
    data = inst / "data"
    storage = root / "state/secrets"
    storage.mkdir(parents=True, exist_ok=True)
    inst.mkdir(parents=True, exist_ok=True)
    dest = data / "cmd_config.json"
    pw = secret(storage / "dashboard-initial-password")
    token = secret(storage / "onebot-token")
    env = {**os.environ, "ASTRBOT_ROOT": str(inst), "ASTRBOT_DASHBOARD_INITIAL_PASSWORD": pw}
    if not (inst / ".astrbot").exists() or not dest.is_file():
        subprocess.run([sys.executable, "-m", "astrbot.cli", "init", "-y"], cwd=inst, env=env, check=True)
    cfg = json.loads(dest.read_text(encoding="utf-8-sig"))
    cfg["dashboard"].update(host="127.0.0.1", port=6185)
    existing = {p["id"] for p in cfg.get("platform", [])}
    if "qq-personal" not in existing:
        cfg.setdefault("platform", []).append(dict(id="qq-personal", type="aiocqhttp", enable=True,
            ws_reverse_host="127.0.0.1", ws_reverse_port=6199, ws_reverse_token=token))
    if "wechat-si" not in existing:
        cfg.setdefault("platform", []).append(dict(id="wechat-si", type="wechat_native", enable=False,
            server_url="http://127.0.0.1:6175", account_id="si", token="",
            token_file=str(storage / "wechat-api"), unique_session=True, allowed_groups=[],
            group_reply_mode="disabled", group_reply_style="plain", enable_private_reply=True,
            private_reply_model_operator_only=True, enable_media=True, enable_group_image_reply=True,
            enable_group_video_and_documents=True, enable_history_tools=True))
    cfg["timezone"] = "Asia/Shanghai"
    save(dest, cfg)
    name = "astrbot_plugin_qoder_neko"
    plugin = data / "config" / (name + "_config.json")
    if not plugin.exists():
        save(plugin, dict(model_id="dfmodel", effort="max", context_window=1000000,
            qoder_use_system_proxy=False, enable_web_search=True, remember_group_jokes=True,
            enable_group_reminders=True, enable_group_game_rooms=True, qq_group_reply_chance=20,
            enable_feedback_codex=False, feedback_workspace=str(work / name),
            feedback_project_source_dirs=[str(work / "astrbot_plugin_wechat_native"), str(work / "si-game-room-source")]))
    onebot = root / "state/napcat-config"
    if not (onebot / "webui.json").exists():
        save(onebot / "webui.json", dict(host="127.0.0.1", port=6100, token=secrets.token_urlsafe(32)))
    if not (onebot / "onebot11.json").exists():
        client = dict(name="AstrBot", enable=True, url="ws://127.0.0.1:6199/ws", token=token,
                      messagePostFormat="array", reportSelfMessage=False, reconnectInterval=5000,
                      debug=False, heartInterval=30000)
        save(onebot / "onebot11.json", {"network": {"httpServers": [], "httpClients": [],
             "httpSseServers": [], "websocketServers": [], "websocketClients": [client], "plugins": []}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/srv/sibot"))
    initialize(parser.parse_args().root.resolve())
