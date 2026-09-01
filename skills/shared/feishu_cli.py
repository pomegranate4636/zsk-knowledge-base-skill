"""飞书 CLI 的最小参数数组执行器；不记录命令参数或输出。"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CliResponse:
    returncode: int
    stdout: str
    stderr: str = ""


class CliRunner(Protocol):
    def run(self, argv: Sequence[str], *, stdin: str | None = None) -> CliResponse: ...

    def upload(self, argv: Sequence[str], *, payload: bytes, name: str) -> CliResponse: ...

    def download(self, argv: Sequence[str], *, name: str) -> tuple[CliResponse, bytes]: ...


class SubprocessCliRunner:
    """以参数数组调用 CLI，绝不经由 shell。"""

    @staticmethod
    def _resolve_argv(argv: Sequence[str]) -> tuple[str, ...]:
        """Windows 上把无扩展名的 shim 名解析为可执行的 .cmd/.exe 变体。

        npm 安装的 bin 常是 `#!/bin/sh` 无扩展名 shim，CreateProcess 无法直接
        启动；同目录的 .cmd/.bat/.exe 才是 Windows 可执行入口。POSIX 环境原样返回。
        """
        if os.name != "nt" or not argv:
            return tuple(argv)
        head = argv[0]
        separators = tuple(separator for separator in (os.sep, os.altsep) if separator)
        if any(separator in head for separator in separators) or head.lower().endswith((".exe", ".cmd", ".bat", ".com")):
            return tuple(argv)
        resolved = shutil.which(head)
        if not resolved:
            return tuple(argv)
        if resolved.lower().endswith((".exe", ".cmd", ".bat", ".com")):
            return (resolved,) + tuple(argv[1:])
        for ext in (".cmd", ".exe", ".bat"):
            candidate = resolved + ext
            if os.path.isfile(candidate):
                return (candidate,) + tuple(argv[1:])
        return (resolved,) + tuple(argv[1:])

    def run(self, argv: Sequence[str], *, stdin: str | None = None) -> CliResponse:
        return self._run(argv, stdin=stdin)

    @staticmethod
    def _run(argv: Sequence[str], *, stdin: str | None = None, cwd: str | None = None) -> CliResponse:
        try:
            completed = subprocess.run(
                list(SubprocessCliRunner._resolve_argv(argv)),
                check=False,
                shell=False,
                capture_output=True,
                text=True,
                input=stdin,
                timeout=30,
                cwd=cwd,
            )
        except FileNotFoundError:
            return CliResponse(127, "", "lark-cli not found")
        except subprocess.TimeoutExpired:
            return CliResponse(124, "", "lark-cli timed out")
        return CliResponse(completed.returncode, completed.stdout, completed.stderr)

    def upload(self, argv: Sequence[str], *, payload: bytes, name: str) -> CliResponse:
        safe_name = Path(name).name
        if safe_name != name or not safe_name:
            return CliResponse(2, "", "unsafe upload name")
        with tempfile.TemporaryDirectory(prefix="zsk-upload-") as directory:
            path = Path(directory) / safe_name
            path.write_bytes(payload)
            relative_path = f"./{safe_name}"
            return self._run(tuple(relative_path if part == "{file}" else part for part in argv), cwd=directory)

    def download(self, argv: Sequence[str], *, name: str) -> tuple[CliResponse, bytes]:
        safe_name = Path(name).name
        if safe_name != name or not safe_name:
            return CliResponse(2, "", "unsafe download name"), b""
        with tempfile.TemporaryDirectory(prefix="zsk-download-") as directory:
            relative_path = f"./{safe_name}"
            response = self._run(
                tuple(relative_path if part == "{output}" else part for part in argv), cwd=directory
            )
            path = Path(directory) / safe_name
            if response.returncode != 0 or not path.is_file():
                return response, b""
            try:
                return response, path.read_bytes()
            except OSError:
                return CliResponse(1, response.stdout, "downloaded media cannot be read"), b""


@dataclass(frozen=True)
class RecordedCliCall:
    """测试用的脱敏录制响应；argv 必须逐项匹配。"""

    argv: tuple[str, ...]
    stdout: str
    returncode: int = 0
    stderr: str = ""
    stdin: str | None = None
    payload: bytes | None = None
    upload_name: str | None = None
    download_payload: bytes | None = None
    download_name: str | None = None


class RecordedCliRunner:
    """声明式 fake runner，不执行外部 CLI。"""

    def __init__(self, calls: Sequence[RecordedCliCall]) -> None:
        self._calls = tuple(calls)
        self._cursor = 0
        self.calls: list[tuple[str, ...]] = []

    @property
    def exhausted(self) -> bool:
        return self._cursor == len(self._calls)

    def run(self, argv: Sequence[str], *, stdin: str | None = None) -> CliResponse:
        actual = tuple(argv)
        self.calls.append(actual)
        if self._cursor >= len(self._calls):
            return CliResponse(2, "", "unexpected lark-cli call")
        expected = self._calls[self._cursor]
        self._cursor += 1
        if actual != expected.argv or stdin != expected.stdin:
            return CliResponse(2, "", "unexpected lark-cli arguments")
        return CliResponse(expected.returncode, expected.stdout, expected.stderr)

    def upload(self, argv: Sequence[str], *, payload: bytes, name: str) -> CliResponse:
        actual = tuple(argv)
        self.calls.append(actual)
        if self._cursor >= len(self._calls):
            return CliResponse(2, "", "unexpected lark-cli upload")
        expected = self._calls[self._cursor]
        self._cursor += 1
        if actual != expected.argv or payload != expected.payload or name != expected.upload_name:
            return CliResponse(2, "", "unexpected lark-cli upload arguments")
        return CliResponse(expected.returncode, expected.stdout, expected.stderr)

    def download(self, argv: Sequence[str], *, name: str) -> tuple[CliResponse, bytes]:
        actual = tuple(argv)
        self.calls.append(actual)
        if self._cursor >= len(self._calls):
            return CliResponse(2, "", "unexpected lark-cli download"), b""
        expected = self._calls[self._cursor]
        self._cursor += 1
        if actual != expected.argv or name != expected.download_name:
            return CliResponse(2, "", "unexpected lark-cli download arguments"), b""
        return CliResponse(expected.returncode, expected.stdout, expected.stderr), expected.download_payload or b""
