#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
VENV_PYTHON = HOME / "tryx-linux" / ".venv" / "bin" / "python3"
LOOP_SCRIPT = HOME / "tryx_loop_media.py"
CACHE_DIR = HOME / ".cache" / "tryx-display-manager"
PID_FILE = CACHE_DIR / "display-loop.pid"
LOG_FILE = CACHE_DIR / "display-loop.log"


def read_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def is_tryx_loop_process(pid: int) -> bool:
    if pid == os.getpid():
        return False
    target = str(LOOP_SCRIPT.resolve())
    return target in read_cmdline(pid)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def matching_loop_pids() -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if is_tryx_loop_process(pid):
            pids.append(pid)
    return sorted(set(pids))


def stop_pid(pid: int) -> None:
    if not process_alive(pid):
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

    deadline = time.monotonic() + 2.0
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    if process_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def stop_loop() -> int:
    pids = set(matching_loop_pids())
    tracked = read_pid()
    if tracked is not None and is_tryx_loop_process(tracked):
        pids.add(tracked)

    if not pids:
        PID_FILE.unlink(missing_ok=True)
        print("No TRYX display loop is running.")
        return 0

    for pid in sorted(pids):
        stop_pid(pid)
        print(f"Stopped TRYX display loop PID {pid}.")

    PID_FILE.unlink(missing_ok=True)
    return 0


def start_loop(interval: float) -> int:
    if interval < 1.0:
        raise RuntimeError("Loop interval must be at least 1 second")
    if not VENV_PYTHON.is_file():
        raise RuntimeError(f"Missing Python environment: {VENV_PYTHON}")
    if not LOOP_SCRIPT.is_file():
        raise RuntimeError(f"Missing proven loop script: {LOOP_SCRIPT}")

    stop_loop()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    with LOG_FILE.open("a", buffering=1) as log:
        log.write(
            f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} starting loop "
            f"at {interval:.3f} seconds\n"
        )
        process = subprocess.Popen(
            [
                str(VENV_PYTHON),
                str(LOOP_SCRIPT),
                "--interval",
                f"{interval:.3f}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    PID_FILE.write_text(str(process.pid))
    time.sleep(0.35)

    if process.poll() is not None:
        PID_FILE.unlink(missing_ok=True)
        raise RuntimeError(
            f"The TRYX display loop exited with status {process.returncode}. "
            f"See {LOG_FILE}"
        )

    print(
        f"TRYX display loop started with PID {process.pid} "
        f"at {interval:.3f} seconds."
    )
    print(f"Log: {LOG_FILE}")
    return 0


def show_status() -> int:
    pids = matching_loop_pids()
    if not pids:
        PID_FILE.unlink(missing_ok=True)
        print("TRYX display loop: stopped")
        return 1

    print("TRYX display loop: running")
    for pid in pids:
        print(f"PID {pid}: {' '.join(read_cmdline(pid))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Start or stop the proven TRYX field-402 software loop in the "
            "background."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--interval", type=float, default=2.95)
    args = parser.parse_args()

    try:
        if args.start:
            return start_loop(args.interval)
        if args.stop:
            return stop_loop()
        return show_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
