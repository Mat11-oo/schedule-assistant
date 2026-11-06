#!/usr/bin/env python3
"""macOS local launcher with workspace ownership checks and private runtime files."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "launcher"
STATE = RUNTIME / "state.json"
LOG = RUNTIME / "server.log"
PYTHON = ROOT / ".venv" / "bin" / "python"
PORT = 8787
URL = f"http://127.0.0.1:{PORT}"


class LauncherError(Exception):
    pass


def command_output(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=4,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode and result.stderr.strip():
        raise LauncherError("系统进程信息暂时无法读取，请核对本机终端权限。")
    return result.stdout if result.returncode == 0 else ""


def inspect_owned_process(pid):
    """Use independent OS data before trusting a saved PID or sending a signal."""
    if not isinstance(pid, int) or pid <= 1:
        return None
    owner = command_output(["ps", "-p", str(pid), "-o", "uid="]).strip()
    if owner != str(os.getuid()):
        return None
    arguments = command_output(["ps", "-p", str(pid), "-o", "command="]).strip()
    try:
        tokens = shlex.split(arguments)
    except ValueError:
        return None
    uvicorn_command = "uvicorn" in tokens or bool(tokens and Path(tokens[0]).name == "uvicorn")
    if not uvicorn_command or "app.main:app" not in tokens:
        return None
    cwd_output = command_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    directories = [line[1:] for line in cwd_output.splitlines() if line.startswith("n")]
    if len(directories) != 1 or Path(directories[0]).resolve() != ROOT:
        return None
    stamp = command_output(["ps", "-p", str(pid), "-o", "lstart="]).strip()
    if not stamp:
        return None
    return {"pid": pid, "start_stamp": stamp, "workspace": str(ROOT), "port": PORT}


def listener_pids():
    output = command_output(["lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-t"])
    return sorted({int(line) for line in output.splitlines() if line.isdigit()})


def saved_state():
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(value):
    fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=RUNTIME)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, STATE)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def launcher_lock():
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(RUNTIME, 0o700)
    lock_path = RUNTIME / "launcher.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def ensure_local_config():
    from dotenv import dotenv_values

    path = ROOT / ".env"
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("AUTH_SECRET=" + secrets.token_urlsafe(48) + "\n")
            stream.write("DATA_DIR=./data/local\nPROACTIVE_ENABLED=1\nPROACTIVE_TICK_SECONDS=10\nPROACTIVE_TZ=Asia/Shanghai\n")
        from configure_bailian import save_config
        save_config(path)
        created = True
    config = dotenv_values(path)
    auth = os.environ.get("AUTH_SECRET", config.get("AUTH_SECRET") or "")
    if not auth.strip():
        raise LauncherError("请先补齐本机 AUTH_SECRET，配置步骤见 docs/local-use.md。")
    return created


def healthy():
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(URL + "/health", timeout=1.5) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
        return payload.get("ok") is True and payload.get("service") == "personal-agent"
    except (OSError, ValueError):
        return False


def wait_for_start(process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = inspect_owned_process(process["pid"])
        if current is None or current["start_stamp"] != process["start_stamp"]:
            raise LauncherError("服务进程已退出，请打开 data/launcher/server.log 查看启动记录。")
        listeners = listener_pids()
        if listeners and listeners != [process["pid"]]:
            raise LauncherError("8787 端口的运行进程发生变化，请核对后重新启动。")
        if listeners and healthy():
            return
        time.sleep(0.4)
    raise LauncherError("服务仍在启动或配置需要核对，请查看 data/launcher/server.log，再运行 status 查看状态。")


def tracked_process():
    saved = saved_state()
    current = inspect_owned_process(saved.get("pid"))
    if current and current["start_stamp"] == saved.get("start_stamp"):
        return current
    return None


def open_chrome(path):
    target = URL + path
    result = subprocess.run(["open", "-a", "Google Chrome", target], capture_output=True, timeout=8)
    if result.returncode:
        print("请在 Chrome 中打开 " + target)


def start(open_browser=True):
    if not PYTHON.is_file():
        raise LauncherError("请先准备项目 .venv 和依赖，安装步骤见 docs/local-use.md。")
    first_config = ensure_local_config()
    listeners = listener_pids()
    process = None
    if listeners:
        if len(listeners) != 1:
            raise LauncherError("8787 端口有多个监听进程，请核对端口归属后启动。")
        process = inspect_owned_process(listeners[0])
        if process is None:
            raise LauncherError("8787 端口正由其他程序使用，请先处理端口占用。")
        print("已找到本项目服务，正在读取运行状态。")
    else:
        process = tracked_process()
    if process is None:
        fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "ab") as log:
            child = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(PORT), "--env-file", ".env"], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        inspect_deadline = time.monotonic() + 3
        while process is None and child.poll() is None and time.monotonic() < inspect_deadline:
            process = inspect_owned_process(child.pid)
            if process is None:
                time.sleep(0.1)
        if process is None:
            if child.poll() is not None:
                raise LauncherError("服务启动失败，请查看 data/launcher/server.log。")
            if child.poll() is None:
                stamp = command_output(["ps", "-p", str(child.pid), "-o", "lstart="]).strip()
                write_state({"pid": child.pid, "start_stamp": stamp, "workspace": str(ROOT), "port": PORT})
                child.terminate()
                child.wait(timeout=10)
            raise LauncherError("进程信息暂时无法读取，请核对本机 lsof 与终端权限。")
        print("正在启动 Personal Agent。")
    write_state(process)
    wait_for_start(process)
    print("Personal Agent 已运行，进程 " + str(process["pid"]))
    print(URL + "/#home")
    print("后台日志保存在 " + str(LOG))
    if first_config:
        print("已生成本机登录配置和空密钥的百炼预设。")
    if open_browser:
        open_chrome("/#setup" if first_config else "/#home")


def stop():
    process = tracked_process()
    if process is None:
        listeners = listener_pids()
        if not listeners:
            STATE.unlink(missing_ok=True)
            print("Personal Agent 已停止。")
            return
        if len(listeners) == 1:
            process = inspect_owned_process(listeners[0])
        if process is None:
            raise LauncherError("端口进程的归属需要核对，请从系统中确认后再停止。")
    current = inspect_owned_process(process["pid"])
    if current is None or current["start_stamp"] != process["start_stamp"]:
        raise LauncherError("进程信息已变化，请重新查看运行状态。")
    write_state(process)
    os.kill(process["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        current = inspect_owned_process(process["pid"])
        if current is None or current["start_stamp"] != process["start_stamp"]:
            STATE.unlink(missing_ok=True)
            print("Personal Agent 已停止，资料和配置保存在本机。")
            return
        time.sleep(0.3)
    raise LauncherError("服务仍在关闭，请稍后运行 status 查看状态。")


def status():
    listeners = listener_pids()
    if len(listeners) == 1:
        process = inspect_owned_process(listeners[0])
        if process:
            print("Personal Agent " + ("已就绪" if healthy() else "正在加载或配置需要核对"))
            print(URL + "/#setup")
            print("进程 " + str(process["pid"]))
            return
    if tracked_process():
        print("Personal Agent 正在启动或关闭，可查看 " + str(LOG))
    elif listeners:
        print("8787 端口正在被其他程序使用。")
    else:
        print("Personal Agent 已停止。")


def main():
    parser = argparse.ArgumentParser(description="Personal Agent 本机启动、停止和状态")
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--no-open", action="store_true", help="启动后在终端显示访问地址")
    parser.add_argument("--pause", action="store_true", help="完成后等待回车，适用于 Finder 双击")
    args = parser.parse_args()
    result = 0
    try:
        if shutil.which("lsof") is None or shutil.which("ps") is None:
            raise LauncherError("请先确认本机可以使用 lsof 和 ps 命令。")
        with launcher_lock():
            if args.action == "start":
                start(open_browser=not args.no_open)
            elif args.action == "stop":
                stop()
            else:
                status()
    except LauncherError as exc:
        print(str(exc), file=sys.stderr)
        result = 1
    except (OSError, subprocess.SubprocessError):
        print("本机操作未完成，请核对目录写入权限、进程状态和启动日志。", file=sys.stderr)
        result = 1
    except KeyboardInterrupt:
        print("操作已中断，可使用 status 查看服务状态。", file=sys.stderr)
        result = 1
    if args.pause and sys.stdin.isatty():
        try:
            input("\n按回车关闭本窗口。")
        except (EOFError, KeyboardInterrupt):
            pass
    return result


if __name__ == "__main__":
    raise SystemExit(main())
