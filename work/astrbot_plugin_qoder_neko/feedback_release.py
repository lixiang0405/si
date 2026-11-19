"""Confirm completed feedback and deploy it from an authorized private chat."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from astrbot.api.web import json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_root

from .feedback_store import FeedbackReleaseError
from .reply_texts import si_reply


RELEASE_STATUS = {"queued": "等待部署", "running": "正在部署", "deployed": "部署成功", "failed": "部署失败"}
HOST_DEPLOY_SOCKET = "/run/sibot-deploy/control.sock"


def submit_host_release(release_id: int) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(HOST_DEPLOY_SOCKET)
        connection.sendall(json.dumps({"op": "deploy", "release_id": release_id}).encode() + b"\n")
        with connection.makefile("rb") as response:
            line = response.readline(4097)
    if len(line) > 4096 or not line.endswith(b"\n"):
        raise ValueError("Invalid host deployment response")
    result = json.loads(line)
    if (not isinstance(result, dict) or result.get("ok") is not True
            or result.get("release_id") != release_id or result.get("status") not in {"queued", "running"}):
        raise ValueError("Host deployment request was not accepted")


def fail_unclaimed_release(store, release_id: int, message: str) -> bool:
    # A lost RPC response can race with the host worker claiming the queue row.
    # Leave claimed work under the host worker's transaction and recovery logic.
    with closing(sqlite3.connect(store.path, timeout=1)) as database, database:
        return database.execute(
            "UPDATE feedback_releases SET status='failed', result=?, updated_at=? "
            "WHERE id=? AND status='queued'",
            (message, time.time(), release_id),
        ).rowcount == 1


def confirmation_label(record: dict) -> str:
    if record["release_id"] is not None:
        return si_reply("已部署 · 批次 #{release_id}", release_id=record['release_id'])
    if record["confirmed_at"] is not None:
        return si_reply("已确认 · 等待部署")
    return si_reply("待确认完成")


def release_status(store) -> str:
    batch = store.release()
    if not batch:
        return si_reply("还没有部署记录。")
    numbers = "、".join(f"#{n}" for n in json.loads(batch["tickets"]))
    message = si_reply("部署 #{id} · {value}\n工单 {numbers}", id=batch['id'], value=RELEASE_STATUS[batch['status']], numbers=numbers)
    return f"{message}\n{batch['result']}" if batch["result"] else message


def start_release(store, workspace: Path, reviewer: str, *, context=None) -> str:
    instance = Path(get_astrbot_root()).resolve()
    root = instance.parent.parent
    expected = (root / "work" / "astrbot_plugin_qoder_neko").resolve()
    runner = root / "outputs" / "Deploy-SiBotFeedback.py"
    if instance != root / "work" / "astrbot-instance" or not runner.is_file():
        raise FeedbackReleaseError(si_reply("找不到这套安装的反馈部署程序。"))
    if workspace != expected:
        raise FeedbackReleaseError(si_reply("私聊部署只适用于当前安装的祀插件源码目录。"))
    if sys.platform == "linux":
        batch = store.queue_release(str(workspace), reviewer)
        try:
            submit_host_release(batch["id"])
        except (OSError, ValueError, UnicodeError):
            message = si_reply("宿主部署服务暂时无法连接。确认状态已保留，可以重试。")
            if fail_unclaimed_release(store, batch["id"], message):
                raise FeedbackReleaseError(message) from None
        return si_reply("部署 #{id} 已交给宿主部署服务。用 /反馈 部署状态 查看结果。", id=batch['id'])
    if os.name != "nt":
        raise FeedbackReleaseError(si_reply("当前系统没有可用的反馈部署入口。"))
    batch = store.queue_release(str(workspace), reviewer)
    try:
        subprocess.Popen(
            [sys.executable, "-B", str(runner), str(batch["id"])], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError:
        store.finish_release(batch["id"], False, si_reply("本机部署程序启动失败。确认状态已保留，可以重试。"))
        raise FeedbackReleaseError(si_reply("本机部署程序启动失败，请查看 /反馈 部署状态。")) from None
    return si_reply("部署 #{id} 已受理，祀会短暂掉线。恢复后发 /反馈 部署状态 查看结果。", id=batch['id'])


def register_confirmation_view(plugin) -> None:
    register = getattr(plugin.context, "register_web_api", None)
    if not callable(register):
        return

    def response(data, status=200):
        return json_response(data, status_code=status, headers={"Cache-Control": "no-store"})

    def administrator():
        dashboard = plugin.context.get_config().get("dashboard") or {}
        return request.username and request.username == dashboard.get("username")

    async def tickets():
        if not administrator():
            return response({"message": si_reply("请用 AstrBot 管理员账号登录。")}, 403)
        store = plugin.feedback_store()
        rows = store.list("completed", limit=50)
        return response({"tickets": [
            {"id": row["id"], "report": row["report"], "result": row["result"][:1000],
             "revision": row["updated_at"], "status": confirmation_label(row),
             "can_confirm": row["confirmed_at"] is None and row["release_id"] is None}
            for row in rows
        ], "deployment": release_status(store)})

    async def confirm():
        if not administrator():
            return response({"message": si_reply("请用 AstrBot 管理员账号登录。")}, 403)
        data = await request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get("id")) is not int or type(data.get("revision")) not in {float, int}:
            return response({"message": si_reply("请刷新工单后重试。")}, 400)
        try:
            confirmed = plugin.feedback_store().confirm(data["id"], data["revision"])
        except (sqlite3.Error, OverflowError):
            return response({"message": si_reply("确认状态暂时无法保存，请重试。")}, 409)
        if not confirmed:
            return response({"message": si_reply("工单状态已变更，请刷新后重新确认。")}, 409)
        return response({"message": si_reply("工单 #{id} 已确认完成。", id=data['id'])})

    prefix = "/astrbot_plugin_qoder_neko/feedback-release"
    register(prefix + "/tickets", tickets, ["GET"], "查看反馈确认和部署状态")
    register(prefix + "/confirm", confirm, ["POST"], "确认反馈工单已完成")
