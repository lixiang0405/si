# 源码来源与许可

本项目自有的机器人逻辑、棋牌引擎与部署脚本采用根目录的 [MIT 许可证](LICENSE)。下表给出了复现这一版本所需的上游来源。

| 组件 | 本仓库提供的内容 | 来源与许可 |
| --- | --- | --- |
| AstrBot 4.29.4 | Python 安装版本、依赖约束、可选的完整源码下载脚本 | 上游发行包中的 `LICENSE` 为 AGPL-3.0。[PyPI 发行包](https://pypi.org/project/astrbot/4.29.4/) |
| AstrBot 微信适配器 | 生产使用的 0.3.16+si.1 源码、测试、界面和文档 | 保留 neo.zhu 与 Wings-030 的 [MIT 许可](work/astrbot_plugin_wechat_native/LICENSE)。[上游项目](https://github.com/neozhu/astrbot-plugin-wechat-native) |
| agent-wechat v2.3.2 | 固定基线与 13 个文件的增量补丁，含事件流、媒体读取、测试、变更记录与接口文档 | 上游提交 `cdb6635ab698743ee92b9efe62bd54bce9bfe636`。补丁沿用 [MIT 许可](deploy/native/LICENSE)。[完整上游源码](https://github.com/thisnick/agent-wechat/tree/cdb6635ab698743ee92b9efe62bd54bce9bfe636) |
| NapCat 4.18.42 | 安装脚本、OneBot v11 连接配置生成器与源码定位 | 运行包来自对应的公开发行留档。源码提交为 `9335843246e3b7a17944861f3ac8731a259732c2`。复制、修改和再分发适用上游 Limited Redistribution License。[原许可](https://github.com/XingYunStar/NapCatQQ/blob/9335843246e3b7a17944861f3ac8731a259732c2/LICENSE) · [发布包](https://github.com/XingYunStar/NapCatQQ/releases/tag/v4.18.42) |
| 腾讯 Linux QQ 3.2.34-53644 | 从腾讯取得对应 amd64 包的指引和 SHA-256 校验 | 客户端由部署者自行下载并按腾讯条款使用。[腾讯 Linux QQ](https://im.qq.com/linuxqq/index.shtml) |
| neko-skill | 祀的人设沿用技能文件组织方式，保留原声明 | [neko-skill MIT 许可](work/astrbot_plugin_qoder_neko/skills/neko-qq/LICENSE.neko-skill) |
| Codex CLI、Caddy、可选语音模型 | 安装或连接指引 | 使用各自发行包的许可。[Codex](https://github.com/openai/codex) · [Caddy](https://github.com/caddyserver/caddy) · [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) |

《明日方舟：终末地》、祀及官方角色资料的相关权利归原权利人。三国杀相关名称、牌面和规则资料的相关权利归其权利人。本仓库是非官方同人项目。角色技能和玩法说明列有资料来源。随项目提供的网页插画为项目制作的同人素材，根目录的软件许可授权限于贡献者有权许可的内容。[角色资料引用](work/astrbot_plugin_qoder_neko/skills/neko-qq/SKILL.md) · [玩法与规则来源](work/si-game-room-source/README.md)
