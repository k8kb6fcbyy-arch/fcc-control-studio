"""FCC 1.5 environment preflight and *explicit* local setup.

This module intentionally uses the Python standard library only. It never
installs, executes, exposes a server or sends data without an explicit command.
It cannot infer whether the calling AI host has persistent permissions.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

VERSION = "1.5.0"
CREDIT = "FCC Control Studio — Chans (chenchen_wang@foxmail.com)"
DEFAULT_URL = "http://127.0.0.1:8099"


def local_url(value: str) -> str:
    """Only permit explicit loopback HTTP addresses (never remote servers)."""
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("仅允许本地 HTTP 地址，例如 http://127.0.0.1:8099")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("端口无效") from exc
    if port is not None and port < 1:
        raise ValueError("端口无效")
    return value.rstrip("/")


def _read_json(url: str) -> dict:
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=1.5) as response:
            result = json.load(response)
            return result if isinstance(result, dict) else {}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return {}


def locate_core(root: Path | None = None) -> Path | None:
    root = root or Path(__file__).resolve().parents[3]
    candidates = [root / "fcc-core", root]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/fcc/server.py").is_file():
            return candidate.resolve()
    return None


def detect(server: str = DEFAULT_URL, root: Path | None = None) -> dict:
    """Report facts, possible modes and requirements, without starting work."""
    base = local_url(server)
    core_dir = locate_core(root)
    python_compatible = sys.version_info >= (3, 11)
    core_present = core_dir is not None
    installed = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("pydantic") is not None
    health = _read_json(base + "/api/health")
    service_ready = health.get("ok") is True and health.get("service") == "fcc"
    scheduler_running = bool(health.get("scheduler_running")) if service_ready else False
    adapters = _read_json(base + "/api/adapters") if service_ready else {}
    observation_sources = adapters.get("observation_sources", []) if isinstance(adapters.get("observation_sources"), list) else []
    if service_ready and scheduler_running:
        mode = "local_scheduler_available"
        next_action = "先用 /setup 确认目标与数据源，再获得授权激活；报告只保存本地收件箱。"
    elif service_ready:
        mode = "local_service_without_scheduler"
        next_action = "服务可用但后台调度未运行；不能承诺自动周期执行。"
    elif health:
        mode = "service_mismatch"
        next_action = "端口上存在不兼容的服务；不要自动写入或尝试接管。"
    elif core_present and python_compatible:
        mode = "local_setup_available"
        next_action = "有本地源码，获明确许可后安装依赖并在前台启动服务。"
    else:
        mode = "conversation_only"
        next_action = "先在对话中进行目标访谈；不能声称已启动持续监控。"
    return {
        "version": VERSION,
        "source_credit": CREDIT,
        "mode": mode,
        "python": {"version": sys.version.split()[0], "compatible": python_compatible},
        "core": {"source_present": core_present, "directory": str(core_dir) if core_dir else None,
                 "dependencies_detected_in_current_python": installed},
        "service": {"reachable_fcc": service_ready, "scheduler_running": scheduler_running,
                    "last_scheduler_tick": health.get("last_scheduler_tick") if service_ready else None,
                    "version": health.get("version") if service_ready else None,
                    "observation_adapters": observation_sources,
                    "automatic_observation_confirmed": False,
                    "report_delivery": "local_outbox_only"},
        "ai_host": {"has_file_access": "unknown", "can_execute_commands": "unknown",
                    "can_access_loopback": "unknown", "persistent_runtime": "unknown",
                    "can_proactively_message_user": "unknown"},
        "urls": {"start": base + "/start", "setup": base + "/setup", "easy": base + "/easy",
                 "expert": base + "/expert"} if service_ready else {},
        "warnings": [
            "本地 API 不具备生产级身份认证，禁止公开暴露。",
            "检测不代表 AI 宿主拥有访问权限；AI 必须实际验证自己的工具能力。",
            "调度器运行也不意味着已接通观测数据或主动消息推送。",
            "本工具不会自行安装依赖、启动进程或替用户授权控制动作。",
        ],
        "next_action": next_action,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FCC 1.5 本地环境检测与受控部署")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("doctor", "install", "serve"):
        sub = commands.add_parser(action)
        sub.add_argument("--json", action="store_true", help="输出可供 AI 解析的 JSON")
        if action == "doctor":
            sub.add_argument("--server", default=DEFAULT_URL)
        else:
            sub.add_argument("--yes", action="store_true", help="已由用户明确授权安装或启动")
            if action == "serve":
                sub.add_argument("--port", type=int, default=8099)
    args = parser.parse_args(argv)
    if args.action == "doctor":
        try:
            result = detect(args.server)
        except ValueError as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"FCC Bootstrap {VERSION} | mode: {result['mode']}")
            print(result["next_action"])
            print("AI 工具权限尚未确定；不得声称后台运行或自动发送消息。")
        return 0
    if not args.yes:
        parser.error("执行 install/serve 前必须得到用户授权，并明确添加 --yes。")
    core = locate_core()
    if core is None:
        parser.error("未找到 FCC Core 源码；请使用完整整合包。")
    if sys.version_info < (3, 11):
        parser.error("需要 Python 3.11 或更高版本。")
    venv = core / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if args.action == "install":
        if not python.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        print("正在按用户授权安装本地依赖，首次安装可能需要访问软件包仓库。")
        return subprocess.run([str(python), "-m", "pip", "install", "-e", str(core)], check=False).returncode
    if args.action == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("端口超出有效范围。")
        if not python.exists():
            parser.error("先运行 install --yes；不会自动安装依赖。")
        print("FCC 将以前台模式运行；关闭当前进程后不会继续调度或发送报告。", flush=True)
        return subprocess.run([str(python), "-m", "fcc.server", "--host", "127.0.0.1",
                               "--port", str(args.port), "--db", str(core / "fcc.db")],
                              cwd=str(core), check=False).returncode
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
