# 祀 · QQ / 微信群机器人

祀是一个基于 AstrBot 的中文群聊机器人。它在 QQ 和微信里接话、认图、收发文件，按群分别记住梗和外号，也能定时提醒群友。发 `/德州 开桌`、`/川麻 开桌` 或 `/三国杀 开桌`，群友就能在手机浏览器里共用一桌。群反馈可经绑定管理员批准，交给 Codex 修改源码，再由管理员确认发布。

这份手册对应 **Ubuntu 24.04 LTS、amd64、systemd**，适用于从空机开始搭建。准备自己的域名、Qoder 登录令牌、QQ 和微信账号。反馈自动修复另需能登录并使用模型的 Codex 账号。日常运行的参考机器为 4 核、4 GB 内存、60 GB 系统盘。首次 Rust 编译与媒体缓存需要额外的磁盘空间。

## 1. 部署 AstrBot 和 HTTPS 牌桌

给自己的域名添加指向云机公网 IPv4 的 A 记录，放行入站 TCP 443，保留自己的 SSH 管理入口。Caddy 使用 443 完成证书验证。检查 AAAA 记录时，确保对应的 IPv6 也能到达同一台机器。以下命令都在 **Ubuntu 的 Bash** 里运行。

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/lixiang0405/si-group-bot.git
cd si-group-bot
read -rp '牌桌域名，例如 play.example.org：' GAME_HOST
getent ahostsv4 "$GAME_HOST"
sudo bash deploy/install-ubuntu.sh "$GAME_HOST"
curl -fsS http://127.0.0.1:8765/ready
curl -fsS "https://$GAME_HOST/ready"
```

安装器创建 `sibot` 系统账号、Python 3.12 虚拟环境、插件源码和运行副本，安装并启动 AstrBot、牌桌和 Caddy。它为面板和 OneBot 生成独立随机口令。公网探针应返回 HTTP 200。首次签发证书时可用 `sudo journalctl -u sibot-game-https -n 50` 查看进度。首页要求邀请，直接打开根地址返回 403 是预期行为。

设置 Qoder 的服务凭据，每行写一个 systemd 环境变量，例如 `QODER_ACCESS_TOKEN=你的令牌`。

```bash
sudo touch /srv/sibot/state/secrets/qoder.env
sudo chmod 0600 /srv/sibot/state/secrets/qoder.env
sudoedit /srv/sibot/state/secrets/qoder.env
sudo systemctl restart sibot-astrbot
```

在**自己的电脑**另开终端，把面板转发到本地。将示例主机名换成自己的 SSH 地址。登录 AstrBot 的初始用户名为 `astrbot`。

```bash
ssh -N -L 16185:127.0.0.1:6185 -L 16100:127.0.0.1:6100 ubuntu@服务器地址
```

在服务器的私有 SSH 终端执行 `sudo cat /srv/sibot/state/secrets/dashboard-initial-password` 获取初始面板密码，然后打开 [本地 AstrBot 面板](http://127.0.0.1:16185/)。两个插件应能在插件管理中看到。QQ 随安装器启用，微信平台在扫码完成后启用。

## 2. 用 NapCat 接入个人 QQ

使用 **Linux QQ 3.2.34-53644** 和 **NapCat 4.18.42**。下面的安装脚本会校验两个文件的 SHA-256，在 `/srv/siqq` 建立独立运行目录，并给指定的机器人 QQ 号写入 OneBot v11 反向 WebSocket 配置。

```bash
sudo apt-get install -y xvfb xauth libgtk-3-0t64 libasound2t64 libatk-bridge2.0-0t64 libnss3 libgbm1 libxss1 libxkbcommon0 libsecret-1-0 libxtst6 libx11-xcb1 libxrandr2 libcups2t64
mkdir -p downloads
curl -fL --retry 3 -o downloads/linuxqq.deb https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_amd64.deb
curl -fL --retry 3 -o downloads/NapCat.Shell.zip https://github.com/XingYunStar/NapCatQQ/releases/download/v4.18.42/NapCat.Shell.zip
read -rp '用于登录机器人的 QQ 号：' BOT_QQ
sudo bash deploy/install-qq.sh downloads/linuxqq.deb downloads/NapCat.Shell.zip "$BOT_QQ"
sudo python3 -c 'import json; print(json.load(open("/srv/siqq/config/webui.json"))["token"])'
```

在 [本地 NapCat WebUI](http://127.0.0.1:16100/webui/) 填入最后一条命令显示的 **WebUI Token**，再用机器人 QQ 扫码并在手机确认。在网络配置里应能看到已启用的反向客户端 `ws://127.0.0.1:6199/ws`。该连接用的 **OneBot Token** 在 `/srv/sibot/state/secrets/onebot-token`，安装器已配置两端。登录码过期可在同一页面刷新。

用另一个 QQ 私聊机器人发送「请只回复 QQ123」，再在测试群用 QQ 的 @ 功能选中机器人进行第二次测试。若要限制机器人参加的群，在 AstrBot 的平台设置中填写自己的会话白名单，并把管理员私聊会话也列入其中。

## 3. 用 WeChat Native 接入个人微信

```bash
sudo bash deploy/install-native.sh
systemctl is-active sibot-wechat
wx() {
    sudo -u sibot -H /srv/sibot/bin/wx --data-dir /srv/sibot/state/wechat \
        --server http://127.0.0.1:6175 --account si "$@"
}
wx accounts list
# 首次部署、账号列表里还没有 si 时执行
wx accounts create si
wx auth login --strategy qr
wx auth status
```

安装器下载固定版 v2.3.2 源码、核对摘要、应用本项目补丁，并以 `sibot` 身份用 Rust 1.93.1 和两个编译任务构建 `wx`。公网发行的原版二进制缺少这一组媒体和事件流改动。扫码时在手机确认。需要保存图片时可把登录命令改成 `wx auth login --strategy qr --qr-output /srv/sibot/home/wechat-login.png`，在自己的终端查看或取回本次二维码。手机要求实名或人脸时，按微信客户端提示完成验证。

1. 让登录的微信号加入测试群，在群里发一句普通消息。执行 `wx --json groups find '实际群名'`，记下匹配的 `roomId`。如果同名，执行 `wx groups list` 对照群信息。
2. 在 AstrBot 的平台管理打开 `wechat-si`。API 地址填 `http://127.0.0.1:6175`，账号填 `si`，Token 文件填 `/srv/sibot/state/secrets/wechat-api`。首次安装已填好这三项。
3. 把上一步的完整 `roomId` 加入 `allowed_groups`，再把 `group_reply_mode` 设为 `mention`（真正 @ 时唤醒）或 `all`（把普通群消息也交给插件）。开启平台并保存。原适配器将空白名单解释为允许所有群，安装器初始将群回复设为 `disabled`。群媒体、独立会话和历史工具的选项均可在这个平台页面调整。
4. 初始配置仅允许绑定管理员使用微信私聊。先完成下一节的绑定，再分别测一次私聊和群内 @。如果只想收指定好友的消息，也可设置 `allowed_users`。

更新微信服务令牌后，将新令牌以 `sibot:sibot`、`0600` 安装到上述 Token 文件并重启 AstrBot。微信登录态和原生聊天资料在 `/srv/sibot/state/wechat`。

## 4. 绑定管理员，设置人设、模型和群规则

```bash
sudo -u sibot -H /srv/sibot/venv/bin/python /srv/sibot/deploy/pair.py
```

终端会分别给出每个已启用平台的绑定命令，有效期 30 分钟。用自己的 QQ 和微信**私聊对应的机器人**，各发一次输出的 `/模型 绑定 …`。随后用 `/模型 列表` 查看账号实际可用的模型和推理档位。在群里由已绑定的同平台管理员发送 `/模型 qoder 模型ID 档位`，会改变该群的模型。

| 需求 | 配置位置和含义 |
| --- | --- |
| 更换人设 | 编辑工作副本 `/srv/sibot/work/astrbot_plugin_qoder_neko/skills/neko-qq/SKILL.md`。一次手动更新可用 `sudo -u sibot cp /srv/sibot/work/astrbot_plugin_qoder_neko/skills/neko-qq/SKILL.md /srv/sibot/work/astrbot-instance/data/plugins/astrbot_plugin_qoder_neko/skills/neko-qq/SKILL.md` 并重启 AstrBot。 |
| 调整模型 | 在祀插件配置里设置 `model_id`、`effort`、`context_window`。快照默认 `dfmodel`、`max`、`1000000`，可用取值由提供方决定。群内历史上下文和按群长期记忆独立保存。 |
| QQ 主动接话 | `qq_group_reply_chance` 为 0–100，默认 20。真正 @ 和明确指令有单独的处理路径。 |
| 个别群只允许 @ | 在祀插件的 `mention_only_groups` 列表填 `平台实例ID:群ID`，例如 `wechat-si:你的群ID@chatroom`。这些群仍可用 `/反馈`。 |
| 微信普通消息 | 在微信平台选择 `all` 后，由祀插件处理群对话。`reply_to_all_group_messages` 控制插件是否逐条触发。 |
| 梗、待办、游戏、搜索 | 分别配置 `remember_group_jokes`、`enable_group_reminders`、`enable_group_game_rooms`、`enable_web_search`。改完在面板保存。 |

微信本地语音转写为可选项。安装 `sherpa-onnx==1.13.8`、`numpy`，从上游 SenseVoice int8 模型包取出 `model.int8.onnx` 和 `tokens.txt`，放在 `/srv/sibot/work/astrbot-instance/data/plugin_data/astrbot_plugin_qoder_neko/sensevoice/` 并允许 `sibot` 读取，再打开 `enable_wechat_local_stt`。使用此包时 SHA-256 应为 `7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e`。[语音模型包](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2)

## 5. 启用 Codex 反馈修复与发布

```bash
sudo bash deploy/install-codex.sh
sudo -u sibot -H /srv/sibot/bin/codex login --device-auth
sudo -u sibot -H /srv/sibot/bin/codex login status
sudo -u sibot -H /srv/sibot/bin/codex exec --skip-git-repo-check -C /srv/sibot/work/astrbot_plugin_qoder_neko '仅回复 OK'
```

安装的是官方 0.169.0 完整包，含它附带的 Bubblewrap。服务账号的登录目录是 `/srv/sibot/home/.codex`。模型调用通过后，在祀插件配置将 `codex_executable` 填为 `/srv/sibot/bin/codex`，打开 `enable_feedback_codex`。需要在普通会话切换到 Codex 时，再打开 `enable_codex_subscription`。需要单独给 Codex 配代理时，先准备一个可用的本机代理，在上述登录、测试命令和插件配置中都使用 `/srv/sibot/bin/codex-proxy`。随附脚本默认 `127.0.0.1:10808`，代理变量仅作用于其子进程。Qoder 配置为直连。

反馈的主工作目录为 `/srv/sibot/work/astrbot_plugin_qoder_neko`。安装器额外配置微信适配器和小游戏源码。要让工单改 Native，在 `feedback_project_source_dirs` 加入 `/srv/sibot/work/wechat-source/agent-wechat-2.3.2`。要查阅或修改 AstrBot、NapCat 的相同版本，可运行 `sudo bash deploy/fetch-frameworks.sh` 并添加 `/srv/sibot/work/astrbot-source` 和 `/srv/sibot/work/napcat-source`。统一发布器支持祀插件、微信适配器、Native 内核和小游戏。框架和 NapCat 有各自的发布流程。

```bash
sudo systemctl enable --now sibot-deploy
sudo systemctl status sibot-deploy --no-pager
```

在测试群发 `/反馈 一句话说明问题`。在绑定管理员的私聊发 `/反馈 查看 编号`、`/反馈 批准 编号`。可用 `/反馈 模型 编号 模型ID 档位` 选择修复模型。修完看结果，追加要求用 `/反馈 继续 编号 具体要求`，验收用 `/反馈 完成 编号`，最后发一次 `/反馈 部署`，用 `/反馈 部署状态` 看结果。完整发布验收包含微信账号在线检查，涉及小游戏时还检查部署者配置的公网域名。

机器人通过本机 Unix socket `/run/sibot-deploy/control.sock` 提交已批准的批次编号。独立的 root 服务执行备份、切换、健康检查及失败回滚。保持 `/srv/sibot/outputs`、虚拟环境和系统服务文件由 root 管理，反馈工作目录按需授予 `sibot` 写权限。

## 6. 验收、排障和备份

| 验什么 | 怎么确认 |
| --- | --- |
| QQ 和微信收发 | 各自在绑定私聊和一个已允许群发送不同的短口令，群里真正 @ 机器人，手机应看到对应的回复。 |
| 手机牌桌 | 两个群各发 `/德州 开桌`、`/川麻 开桌` 或 `/三国杀 开桌`。在不同手机点群里的完整邀请，第一人入座，第二人申请，由桌主批准。每个群单独开桌，牌局结束后结算回到原群。邀请链接可转发，实际入座由桌主批准。 |
| 待办和记忆 | 在测试群 @ 祀设置几分钟后的提醒，检查到点是否在原群 @ 当事人。不同群的记忆、会话和牌桌分开。 |
| 群邀请打不开 | 先检查 `curl -fsS http://127.0.0.1:8765/ready`，再检查公网 `https://你的域名/ready`、DNS 和 443。桥接端口 8766 仅监听回环。 |
| QQ 在线却无回复 | 在 NapCat 查看账号和反向 WebSocket 状态，核对 6199、OneBot Token、群白名单和唤醒方式。 |
| 微信无回复 | 用 `wx auth status` 查实际账号状态，再查 `wechat-si` 的启用状态、房间 ID、私聊绑定、Token 文件和群触发模式。 |
| 工单发布失败 | 看 `/反馈 部署状态`、`/var/log/sibot-deploy/`、`/srv/sibot/work/feedback-deployments/release-编号/`。排除故障后用 `/反馈 部署` 创建新批次。 |

```bash
systemctl status sibot-{astrbot,napcat,wechat,game,game-https,deploy} --no-pager
sudo journalctl -u sibot-astrbot -u sibot-wechat -u sibot-napcat --since '10 minutes ago' --no-pager
sudo journalctl -u sibot-game-https -u sibot-deploy -n 50 --no-pager
```

将 AstrBot 的 `work/astrbot-instance/data`、微信 `state/wechat`、私有配置 `state/secrets`、QQ 的 `/srv/siqq/home` 与 `/srv/siqq/config`、牌桌 `work/si-game-room/.runtime` 和发布记录备份到私有位置。前述相对路径均以 `/srv/sibot` 为根。对正在写入的 SQLite 使用在线备份，或停对应服务后整体复制数据库与 WAL。重建机器后先恢复数据和权限，再启动服务。

## 目录与开发验证

| 路径 | 内容 |
| --- | --- |
| `work/astrbot_plugin_qoder_neko/` | 对话、模型、人设、历史和群记忆、媒体、待办、反馈、牌桌桥接 |
| `work/astrbot_plugin_wechat_native/` | 实际部署的微信平台适配器，含修改、界面及上游测试 |
| `work/si-game-room-source/` | 手机网页、棋牌游戏引擎、规则测试与对局模拟。[玩法和当前覆盖范围](work/si-game-room-source/README.md) |
| `deploy/` | Ubuntu 安装和绑定脚本、systemd 单元、Caddy、Python 版本约束和 Native 补丁 |
| `outputs/` | 宿主发布器、回滚和测试，以及旧 Windows 工作区的启动辅助脚本。以上复现流程使用 Ubuntu。 |

以下命令在克隆目录运行，使用临时测试数据，不向真实群发消息。

```bash
/srv/sibot/venv/bin/python -B -m unittest discover -s outputs -p 'test_*.py'
/srv/sibot/venv/bin/python -B -m unittest discover -s work/si-game-room-source -p 'test_*.py'
/srv/sibot/venv/bin/python -B work/si-game-room-source/deploy.py --check
```

首个公开版本在 Ubuntu 24.04 的空数据目录完成了两次初始化、两个插件的实际加载、面板 HTTP 200 和本地邀请入座检查。16 项发布器测试、99 项游戏测试通过，游戏测试包含德州、川麻和八种三国杀桌型各一场完整对局。私人账号的扫码和手机收发需要由部署者在自己的环境验收。

自有代码按 MIT 发布。依赖、补丁和同人素材的具体来源见[第三方说明](THIRD_PARTY.md)和[项目许可证](LICENSE)。
