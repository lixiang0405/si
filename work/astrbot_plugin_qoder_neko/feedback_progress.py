"""Summarize completed Codex activity and source changes for one feedback run."""

import asyncio
import os
from pathlib import Path

from .reply_texts import si_reply


FEEDBACK_PROGRESS_INTERVAL = 20 * 60
WORKSPACE_IGNORED = {"__pycache__", "node_modules", "venv"}
CODEX_ACTIVITY = {"command_execution", "file_change", "mcp_tool_call"}


def source_state(workspace: Path, *, include_hidden: bool = False) -> dict[str, tuple[int, int]]:
    state = {}
    is_junction = getattr(os.path, "isjunction", lambda path: False)
    for directory, folders, files in os.walk(workspace):
        root = Path(directory)
        folders[:] = [
            name for name in folders
            if (include_hidden or not name.startswith("."))
            and name != ".git" and name not in WORKSPACE_IGNORED
            and not is_junction(root / name)
        ]
        for name in files:
            if (not include_hidden and name.startswith(".")) or name == ".git" or name.endswith(".pyc"):
                continue
            path = root / name
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                continue
            state[path.relative_to(workspace).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return state


class FeedbackProgress:
    def __init__(
        self, workspace: Path, additional_write_dirs: tuple[Path, ...] = (), project_sources: tuple[Path, ...] = (),
    ):
        self.workspace = workspace
        self.additional_write_dirs = additional_write_dirs
        self.project_sources = project_sources
        self.baseline = self.snapshot()
        self.activity = 0
        self.note = ""

    def snapshot(self) -> dict[str, tuple[int, int]]:
        state = source_state(self.workspace)
        for directory in self.additional_write_dirs:
            files = source_state(directory, include_hidden=directory in self.project_sources)
            state.update((str(directory / name), stat) for name, stat in files.items())
        return state

    def changed_files(self) -> set[str]:
        current = self.snapshot()
        return {name for name in self.baseline.keys() | current.keys() if self.baseline.get(name) != current.get(name)}

    def modified_project_sources(self) -> tuple[str, ...]:
        paths = [Path(name) for name in self.changed_files() if Path(name).is_absolute()]
        return tuple(str(root) for root in self.project_sources if any(path.is_relative_to(root) for path in paths))

    def observe(self, item: dict) -> None:
        kind = item.get("type")
        if kind in CODEX_ACTIVITY:
            self.activity += 1
        elif kind == "agent_message" and isinstance(item.get("text"), str):
            self.note = " ".join(item["text"].split())[:240]

    async def summary(self) -> str:
        changed = sorted(await asyncio.to_thread(self.changed_files))
        lines = []
        if changed:
            examples = "、".join(changed[:6])
            if len(examples) > 160 or len(changed) > 6:
                examples = examples[:160] + "…"
            lines.append(si_reply("本轮工作区已有 {count} 个文件发生变化。{examples}", count=len(changed), examples=examples))
        else:
            lines.append(si_reply("本轮暂未记录到工作区文件变化。"))
        if self.activity:
            lines.append(si_reply("Codex 已完成 {count} 项工具操作。", count=self.activity))
        if self.note:
            lines.append(si_reply("Codex 最新说明\n{note}", note=self.note))
        return "\n".join(lines)
