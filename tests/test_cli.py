import argparse
import socket
import subprocess
import urllib.request

from space_memory.cli import (
    cmd_start,
    cmd_status,
    cmd_stop,
    is_space_memory_process,
    read_pid,
)


def ns(**kwargs):
    return argparse.Namespace(**kwargs)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- pidfile / process helpers ---------------------------------------------


def test_read_pid(tmp_path):
    p = tmp_path / "x.pid"
    p.write_text("1234\n")
    assert read_pid(p) == 1234
    assert read_pid(tmp_path / "missing.pid") is None
    p.write_text("not-a-number")
    assert read_pid(p) is None


def test_is_space_memory_process_false_for_unknown_pid():
    assert is_space_memory_process(999_999_999) is False


def test_stop_refuses_to_kill_non_space_memory_process(tmp_path):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        pidfile = tmp_path / "space-memory.pid"
        pidfile.write_text(str(proc.pid))
        assert cmd_stop(ns(pidfile=str(pidfile))) == 2
        assert proc.poll() is None  # still alive — refused to kill
    finally:
        proc.terminate()
        proc.wait()


# --- full lifecycle ---------------------------------------------------------


def test_start_status_stop_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_MEMORY_TOKEN_PEPPER", "lifecycle-test-pepper")
    monkeypatch.setenv("SPACE_MEMORY_DATABASE_URL", f"sqlite:///{tmp_path / 'lifecycle.db'}")

    port = free_port()
    pidfile = tmp_path / "space-memory.pid"
    logfile = tmp_path / "space-memory.log"

    start_args = ns(pidfile=str(pidfile), logfile=str(logfile), host="127.0.0.1", port=port)
    assert cmd_start(start_args) == 0

    # status reports running and health responds
    assert cmd_status(ns(pidfile=str(pidfile))) == 0
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
        assert resp.status == 200

    # stop terminates the process and removes the pidfile
    assert cmd_stop(ns(pidfile=str(pidfile))) == 0
    assert cmd_status(ns(pidfile=str(pidfile))) == 1
    assert not pidfile.exists()
