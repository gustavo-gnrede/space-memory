from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parent  # src/space_memory
_PROJECT_ROOT = _SRC_DIR.parent.parent  # repository root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def default_pidfile() -> Path:
    return Path(os.environ.get("SPACE_MEMORY_PIDFILE", "data/space-memory.pid"))


def default_logfile() -> Path:
    return Path(os.environ.get("SPACE_MEMORY_LOGFILE", "data/space-memory.log"))


def read_pid(pidfile: Path) -> int | None:
    try:
        return int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _read_cmdline(pid: int) -> list[str] | None:
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        raw = proc_cmdline.read_bytes()
        return [arg.decode("utf-8", "replace") for arg in raw.split(b"\0") if arg]
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip().split()
    return None


def is_space_memory_process(pid: int) -> bool:
    """True only when the PID's command line looks like a Space Memory server.

    This guard prevents killing an unrelated process that happened to reuse a
    stale PID. We never kill by port number.
    """
    cmdline = _read_cmdline(pid)
    if not cmdline:
        return False
    joined = " ".join(cmdline).lower()
    return "space_memory" in joined or "space-memory" in joined


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_healthy(host: str, port: int, proc: subprocess.Popen, timeout: float = 15.0) -> bool:
    url = f"http://{host}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def cmd_start(args) -> int:
    load_dotenv(Path.cwd() / ".env")
    if not os.environ.get("SPACE_MEMORY_TOKEN_PEPPER"):
        print("SPACE_MEMORY_TOKEN_PEPPER is not set (and not found in .env). Set it and retry.", file=sys.stderr)
        return 1

    pidfile = Path(args.pidfile or default_pidfile()).resolve()
    logfile = Path(args.logfile or default_logfile()).resolve()
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.parent.mkdir(parents=True, exist_ok=True)

    existing = read_pid(pidfile)
    if existing and pid_alive(existing):
        if is_space_memory_process(existing):
            print(f"Space Memory already running (pid {existing})")
            return 0
        print(f"Removing stale pidfile (pid {existing} is not a Space Memory process)", file=sys.stderr)
    if existing:
        pidfile.unlink(missing_ok=True)

    cmd = [
        sys.executable, "-m", "uvicorn", "space_memory.main:app",
        "--host", args.host, "--port", str(args.port),
    ]
    env = os.environ.copy()
    src_root = str(_SRC_DIR.parent)
    env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    with open(logfile, "ab") as log_fh:
        proc = subprocess.Popen(
            cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(_PROJECT_ROOT), env=env,
        )
    pidfile.write_text(str(proc.pid))

    if not _wait_healthy(args.host, args.port, proc):
        print(f"Started pid {proc.pid} but the health check did not pass; see {logfile}", file=sys.stderr)
        return 1
    print(f"Space Memory started (pid {proc.pid}, http://{args.host}:{args.port})")
    print(f"Log: {logfile}")
    return 0


def cmd_stop(args) -> int:
    pidfile = Path(args.pidfile or default_pidfile()).resolve()
    pid = read_pid(pidfile)
    if pid is None:
        print("Space Memory is not running (no pidfile)")
        return 0
    if not pid_alive(pid):
        print(f"Space Memory is not running (pid {pid} is dead); removing stale pidfile")
        pidfile.unlink(missing_ok=True)
        return 0
    if not is_space_memory_process(pid):
        print(f"Refusing to stop pid {pid}: it is not a Space Memory process", file=sys.stderr)
        return 2

    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        print(f"pid {pid} did not exit after SIGTERM; sending SIGKILL", file=sys.stderr)
        os.kill(pid, signal.SIGKILL)
    pidfile.unlink(missing_ok=True)
    print(f"Space Memory stopped (pid {pid})")
    return 0


def cmd_status(args) -> int:
    pidfile = Path(args.pidfile or default_pidfile()).resolve()
    pid = read_pid(pidfile)
    if pid is None:
        print("Space Memory: stopped (no pidfile)")
        return 1
    if not pid_alive(pid):
        print(f"Space Memory: stopped (pid {pid} is dead; stale pidfile)")
        return 1
    if not is_space_memory_process(pid):
        print(f"Space Memory: UNKNOWN (pid {pid} exists but is not a Space Memory process)")
        return 1
    print(f"Space Memory: running (pid {pid})")
    return 0


def cmd_restart(args) -> int:
    if cmd_stop(args) == 2:
        return 2
    return cmd_start(args)


def _env_port() -> int:
    raw = os.environ.get("SPACE_MEMORY_PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"SPACE_MEMORY_PORT must be an integer, got {raw!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="space-memory", description="Space Memory lifecycle manager")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_pidfile(p):
        p.add_argument("--pidfile", default=None, help="PID file path (default: data/space-memory.pid)")

    p_start = sub.add_parser("start", help="Start the server in the background")
    add_pidfile(p_start)
    p_start.add_argument("--host", default=os.environ.get("SPACE_MEMORY_HOST", DEFAULT_HOST))
    p_start.add_argument("--port", type=int, default=_env_port())
    p_start.add_argument("--logfile", default=None, help="Log file path (default: data/space-memory.log)")

    p_stop = sub.add_parser("stop", help="Stop the server (verifies PID before signalling)")
    add_pidfile(p_stop)

    p_status = sub.add_parser("status", help="Show whether the server is running")
    add_pidfile(p_status)

    p_restart = sub.add_parser("restart", help="Stop then start")
    add_pidfile(p_restart)
    p_restart.add_argument("--host", default=os.environ.get("SPACE_MEMORY_HOST", DEFAULT_HOST))
    p_restart.add_argument("--port", type=int, default=_env_port())
    p_restart.add_argument("--logfile", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "restart": cmd_restart,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
