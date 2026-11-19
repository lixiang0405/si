"""Generate short-lived, private operator binding commands for enabled adapters."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

data = Path(os.environ.get("ASTRBOT_ROOT", "/srv/sibot/work/astrbot-instance")) / "data"
platforms = json.loads((data / "cmd_config.json").read_text(encoding="utf-8-sig"))["platform"]
dest = data / "plugin_data/astrbot_plugin_qoder_neko/model-pairing.json"
dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
codes = {p["id"]: secrets.token_hex(6).upper() for p in platforms
         if p.get("enable") and p["type"] in {"aiocqhttp", "wechat_native", "qq_official", "qq_official_webhook"}}
if not codes:
    raise SystemExit("请先在 AstrBot 中启用需要绑定的适配器。")
with tempfile.NamedTemporaryFile(mode="w", dir=dest.parent, encoding="utf-8", delete=False) as tmp:
    json.dump({p: {"sha256": hashlib.sha256(code.encode()).hexdigest(), "expires_at": int(time.time()) + 1800}
               for p, code in codes.items()}, tmp)
os.replace(tmp.name, dest)
for platform, code in codes.items():
    print(f"{platform} 私聊发送  /模型 绑定 {code}")
