#!/usr/bin/env python3
"""RoboGame 现场联调单终端控制台。

先 source ROS 2 和工作区环境，再运行：
    python3 tools/field_console.py

控制台只负责启动/停止软件进程和汇总日志，不直接发送运动或机构命令。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Awaitable, Callable, TextIO


DEFAULT_CONFIG = Path(__file__).with_name("field_console.json")
ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: str
    group: str
    autostart: bool = False
    color: str = "reset"

    @classmethod
    def from_dict(cls, raw: dict) -> "ProcessSpec":
        required = ("name", "command", "group")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise ValueError(f"process missing required fields: {', '.join(missing)}")
        color = str(raw.get("color", "reset"))
        if color not in ANSI:
            raise ValueError(f"unknown color {color!r} for process {raw['name']!r}")
        return cls(
            name=str(raw["name"]),
            command=str(raw["command"]),
            group=str(raw["group"]),
            autostart=bool(raw.get("autostart", False)),
            color=color,
        )


def load_specs(path: Path) -> list[ProcessSpec]:
    with path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict) or not isinstance(raw.get("processes"), list):
        raise ValueError("config root must contain a 'processes' list")
    specs = [ProcessSpec.from_dict(item) for item in raw["processes"]]
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate process names: {', '.join(duplicates)}")
    return specs


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    process: asyncio.subprocess.Process | None = None
    reader_task: asyncio.Task | None = None
    started_at: float | None = None
    exit_code: int | None = None
    recent: deque[str] = field(default_factory=lambda: deque(maxlen=80))

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None


EventSink = Callable[[dict], Awaitable[None] | None]


class FieldConsole:
    def __init__(
        self,
        specs: list[ProcessSpec],
        *,
        output: TextIO = sys.stdout,
        event_sink: EventSink | None = None,
    ):
        self.items = {spec.name: ManagedProcess(spec) for spec in specs}
        self.output = output
        self.focus: str | None = None
        self._closing = False
        self._print_lock = asyncio.Lock()
        self.event_sink = event_sink

    async def _emit(self, event: dict) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result

    async def print_line(self, text: str, color: str = "reset") -> None:
        async with self._print_lock:
            use_color = bool(getattr(self.output, "isatty", lambda: False)())
            prefix = ANSI[color] if use_color else ""
            suffix = ANSI["reset"] if use_color else ""
            print(f"{prefix}{text}{suffix}", file=self.output, flush=True)

    async def start(self, name: str) -> None:
        item = self._get(name)
        if item.running:
            await self.print_line(f"[console] {name} 已在运行", "yellow")
            return
        item.exit_code = None
        item.started_at = time.monotonic()
        item.process = await asyncio.create_subprocess_shell(
            item.spec.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        item.reader_task = asyncio.create_task(self._read_output(item))
        await self._emit({"type": "process", "name": name, "state": "running", "pid": item.process.pid})
        await self.print_line(
            f"[console] 已启动 {name} (pid={item.process.pid})", "green"
        )

    async def _read_output(self, item: ManagedProcess) -> None:
        assert item.process is not None and item.process.stdout is not None
        while True:
            raw = await item.process.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            item.recent.append(line)
            await self._emit({"type": "log", "name": item.spec.name, "line": line})
            if self.focus is None or self.focus == item.spec.name:
                await self.print_line(
                    f"[{item.spec.name:<12}] {line}", item.spec.color
                )
        item.exit_code = await item.process.wait()
        await self._emit({"type": "process", "name": item.spec.name, "state": "exited", "exit_code": item.exit_code})
        if not self._closing:
            color = "yellow" if item.exit_code == 0 else "red"
            await self.print_line(
                f"[console] {item.spec.name} 已退出 (code={item.exit_code})", color
            )

    async def stop(self, name: str) -> None:
        item = self._get(name)
        if not item.running:
            await self.print_line(f"[console] {name} 未运行", "yellow")
            return
        assert item.process is not None
        if os.name != "nt":
            os.killpg(item.process.pid, signal.SIGTERM)
        else:
            item.process.terminate()
        try:
            await asyncio.wait_for(item.process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            if os.name != "nt":
                os.killpg(item.process.pid, signal.SIGKILL)
            else:
                item.process.kill()
            await item.process.wait()
        item.exit_code = item.process.returncode
        await self._emit({"type": "process", "name": name, "state": "stopped", "exit_code": item.exit_code})
        await self.print_line(f"[console] 已停止 {name}", "yellow")

    async def start_group(self, group: str) -> None:
        matches = [item for item in self.items.values() if item.spec.group == group]
        if not matches:
            raise ValueError(f"unknown group: {group}")
        for item in matches:
            await self.start(item.spec.name)

    async def stop_group(self, group: str) -> None:
        matches = [item for item in self.items.values() if item.spec.group == group]
        if not matches:
            raise ValueError(f"unknown group: {group}")
        for item in reversed(matches):
            await self.stop(item.spec.name)

    async def show_status(self) -> None:
        await self.print_line("名称           分组        状态       PID", "cyan")
        for item in self.items.values():
            if item.running:
                state = "运行中"
                pid = str(item.process.pid)
            elif item.exit_code is None:
                state = "未启动"
                pid = "-"
            else:
                state = f"退出({item.exit_code})"
                pid = "-"
            await self.print_line(
                f"{item.spec.name:<14} {item.spec.group:<11} {state:<10} {pid}"
            )

    async def show_tail(self, name: str, count: int = 20) -> None:
        item = self._get(name)
        for line in list(item.recent)[-count:]:
            await self.print_line(f"[{name:<12}] {line}", item.spec.color)

    def _get(self, name: str) -> ManagedProcess:
        try:
            return self.items[name]
        except KeyError as exc:
            raise ValueError(f"unknown process: {name}") from exc

    def snapshot(self) -> list[dict]:
        return [
            {
                "name": item.spec.name,
                "group": item.spec.group,
                "running": item.running,
                "pid": item.process.pid if item.running else None,
                "exit_code": item.exit_code,
            }
            for item in self.items.values()
        ]

    async def close(self) -> None:
        self._closing = True
        for name in reversed(list(self.items)):
            if self.items[name].running:
                await self.stop(name)

    async def command_loop(self) -> None:
        await self.print_line(HELP_TEXT, "cyan")
        while True:
            try:
                line = await asyncio.to_thread(input, "field> ")
            except (EOFError, KeyboardInterrupt):
                break
            parts = line.strip().split()
            if not parts:
                continue
            command, *args = parts
            try:
                if command in {"quit", "exit", "q"}:
                    break
                if command == "help":
                    await self.print_line(HELP_TEXT, "cyan")
                elif command == "status":
                    await self.show_status()
                elif command == "start" and len(args) == 1:
                    await self.start(args[0])
                elif command == "stop" and len(args) == 1:
                    await self.stop(args[0])
                elif command == "start-group" and len(args) == 1:
                    await self.start_group(args[0])
                elif command == "stop-group" and len(args) == 1:
                    await self.stop_group(args[0])
                elif command == "focus" and len(args) == 1:
                    if args[0] == "all":
                        self.focus = None
                    else:
                        self._get(args[0])
                        self.focus = args[0]
                    await self.print_line(f"[console] 日志焦点: {args[0]}")
                elif command == "tail" and len(args) in {1, 2}:
                    await self.show_tail(args[0], int(args[1]) if len(args) == 2 else 20)
                else:
                    await self.print_line("命令格式不正确；输入 help 查看帮助", "red")
            except (ValueError, OSError) as exc:
                await self.print_line(f"[console] {exc}", "red")


HELP_TEXT = """命令: status | start <名称> | stop <名称>
      start-group <base|arm|line|observe> | stop-group <分组>
      focus <名称|all> | tail <名称> [行数] | help | quit"""


async def async_main(args: argparse.Namespace) -> int:
    specs = load_specs(args.config)
    console = FieldConsole(specs)
    await console.print_line(f"[console] 配置: {args.config}", "dim")
    try:
        for spec in specs:
            if spec.autostart:
                await console.start(spec.name)
        if args.group:
            await console.start_group(args.group)
        await console.command_loop()
    finally:
        await console.close()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--group", help="启动配置中同名的一组进程")
    return parser.parse_args(argv)


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"field_console: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
