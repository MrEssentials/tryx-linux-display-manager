#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from fractions import Fraction
import os
import random
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

WIDTH = 1280
HEIGHT = 720
DEFAULT_FPS = 60
BITRATE = "4500k"
SAVE_DELAY = 2.8
RESTART_INTERVAL = 2.95
DISPLAY_START_LATENCY = 1.30

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
VENV_PYTHON = APP_DIR / ".venv" / "bin" / "python3"
IMAGE_SCRIPT = APP_DIR / "tryx_upload.py"
VIDEO_UPLOAD_SCRIPT = APP_DIR / "tryx_video_upload.py"
SELECT_SCRIPT = APP_DIR / "tryx_select_media.py"
LOOP_SCRIPT = APP_DIR / "tryx_loop_media.py"
RESTART_SCRIPT = APP_DIR / "tryx_restart_media.py"
SINGLE_LOOP_MANAGER = APP_DIR / "tryx_loop_manager.py"
CACHE_DIR = HOME / ".cache" / "tryx-display-manager"
PID_FILE = CACHE_DIR / "shuffle.pid"
LOG_FILE = CACHE_DIR / "shuffle.log"
ACTIVE_MANIFEST = CACHE_DIR / "shuffle-manifest.json"

_STOP_REQUESTED = False
_CURRENT_CHILD: subprocess.Popen[str] | None = None


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def read_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_playlist_process(pid: int) -> bool:
    if pid == os.getpid():
        return False

    cmdline = read_cmdline(pid)
    if "--run" not in cmdline:
        return False

    # Recognize TRYX shuffle processes even if an older copy was launched
    # from the development tree and the current manager is the installed copy.
    return any(
        Path(argument).name == "tryx_playlist_manager.py"
        for argument in cmdline
    )


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def matching_pids() -> list[int]:
    result: list[int] = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and is_playlist_process(int(entry.name)):
            result.append(int(entry.name))
    return sorted(set(result))


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

    deadline = time.monotonic() + 3.0
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


def stop_playlist() -> int:
    pids = set(matching_pids())
    tracked = read_pid()
    if tracked is not None and is_playlist_process(tracked):
        pids.add(tracked)

    if not pids:
        PID_FILE.unlink(missing_ok=True)
        print("No TRYX shuffle is running.")
        return 0

    for pid in sorted(pids):
        stop_pid(pid)
        print(f"Stopped TRYX shuffle PID {pid}.")

    PID_FILE.unlink(missing_ok=True)
    return 0


def start_playlist(manifest: Path) -> int:
    if not manifest.is_file():
        raise RuntimeError(f"Playlist manifest was not found: {manifest}")
    if not VENV_PYTHON.is_file():
        raise RuntimeError(f"Missing Python environment: {VENV_PYTHON}")

    stop_playlist()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, ACTIVE_MANIFEST)

    with LOG_FILE.open("a", buffering=1) as output:
        output.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} starting shuffle\n")
        process = subprocess.Popen(
            [str(VENV_PYTHON), str(Path(__file__).resolve()), "--run", str(ACTIVE_MANIFEST)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            text=True,
        )

    PID_FILE.write_text(str(process.pid))
    time.sleep(0.45)
    if process.poll() is not None:
        PID_FILE.unlink(missing_ok=True)
        raise RuntimeError(
            f"The shuffle process exited with status {process.returncode}. See {LOG_FILE}"
        )

    print(f"TRYX shuffle started with PID {process.pid}.")
    print(f"Log: {LOG_FILE}")
    return 0


def show_status() -> int:
    pids = matching_pids()
    if not pids:
        PID_FILE.unlink(missing_ok=True)
        print("TRYX shuffle: stopped")
        return 1
    print("TRYX shuffle: running")
    for pid in pids:
        print(f"PID {pid}: {' '.join(read_cmdline(pid))}")
    print(f"Log: {LOG_FILE}")
    return 0


def make_filter(
    item: dict[str, Any],
    *,
    video: bool,
    target_fps: int = DEFAULT_FPS,
) -> str:
    fit = str(item.get("fit", "crop"))
    zoom = float(item.get("zoom", 1.0))
    anchor_x = float(item.get("anchor_x", 0.5))
    anchor_y = float(item.get("anchor_y", 0.5))
    rotation = int(item.get("rotation", 0)) % 360

    filters: list[str] = []
    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.extend(["transpose=1", "transpose=1"])
    elif rotation == 270:
        filters.append("transpose=2")

    z = f"{zoom:.6f}"
    ax = f"{anchor_x:.6f}"
    ay = f"{anchor_y:.6f}"

    if fit == "fit":
        factor = f"min({WIDTH}/iw\\,{HEIGHT}/ih)*{z}"
        sw = f"trunc(iw*({factor})/2)*2"
        sh = f"trunc(ih*({factor})/2)*2"
    elif fit == "stretch":
        sw = f"trunc({WIDTH}*{z}/2)*2"
        sh = f"trunc({HEIGHT}*{z}/2)*2"
    else:
        factor = f"max({WIDTH}/iw\\,{HEIGHT}/ih)*{z}"
        sw = f"trunc(iw*({factor})/2)*2"
        sh = f"trunc(ih*({factor})/2)*2"

    filters.extend([
        f"scale=w='{sw}':h='{sh}'",
        (
            f"pad=w='max(iw,{WIDTH})':h='max(ih,{HEIGHT})':"
            f"x='if(lt(iw,{WIDTH}),({WIDTH}-iw)*{ax},0)':"
            f"y='if(lt(ih,{HEIGHT}),({HEIGHT}-ih)*{ay},0)':color=black"
        ),
        (
            f"crop={WIDTH}:{HEIGHT}:"
            f"x='if(gt(iw,{WIDTH}),(iw-{WIDTH})*{ax},0)':"
            f"y='if(gt(ih,{HEIGHT}),(ih-{HEIGHT})*{ay},0)'"
        ),
        "setsar=1",
    ])
    if video:
        filters.extend([f"fps={target_fps}", "format=yuv420p"])
    return ",".join(filters)


def run_command(command: list[str], watch_text: str | None = None) -> float | None:
    global _CURRENT_CHILD
    if _STOP_REQUESTED:
        raise InterruptedError

    log("$ " + shlex.join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _CURRENT_CHILD = process
    seen_at: float | None = None

    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line.rstrip(), flush=True)
            if watch_text and watch_text in line and seen_at is None:
                seen_at = time.monotonic()
            if _STOP_REQUESTED:
                process.terminate()
                raise InterruptedError
        code = process.wait()
    finally:
        _CURRENT_CHILD = None

    if code != 0:
        raise RuntimeError(f"Command failed ({code}): {shlex.join(command)}")
    return seen_at


def run_command_capture(command: list[str], prefix: str) -> str:
    global _CURRENT_CHILD
    if _STOP_REQUESTED:
        raise InterruptedError

    log("$ " + shlex.join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _CURRENT_CHILD = process
    captured: str | None = None

    assert process.stdout is not None
    try:
        for line in process.stdout:
            stripped = line.rstrip()
            print(stripped, flush=True)
            if stripped.startswith(prefix):
                captured = stripped[len(prefix):].strip()
            if _STOP_REQUESTED:
                process.terminate()
                raise InterruptedError
        code = process.wait()
    finally:
        _CURRENT_CHILD = None

    if code != 0:
        raise RuntimeError(f"Command failed ({code}): {shlex.join(command)}")
    if not captured:
        raise RuntimeError(
            f"Command completed but did not report {prefix!r}: {shlex.join(command)}"
        )
    return captured


def sleep_until(deadline: float) -> None:
    while not _STOP_REQUESTED:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))
    raise InterruptedError


def _fraction_to_float(value: object) -> float:
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return rate if rate > 0 else 0.0


def source_frame_rate(path: Path) -> tuple[float, int]:
    """Detect the source cadence and choose an integer FPS for the TRYX stream.

    The old build converted every video to 60 FPS. That duplicates frames in
    24/25/30 FPS footage and can make motion look uneven even though playback
    length is correct. The TRYX metadata stores an integer FPS, so common rates
    such as 23.976, 29.97, and 59.94 are normalized to 24, 30, and 60.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate,r_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not inspect source frame rate: {path}\n{result.stderr.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse source frame rate: {path}") from exc

    streams = payload.get("streams") or []
    stream = streams[0] if streams else {}
    average = _fraction_to_float(stream.get("avg_frame_rate"))
    nominal = _fraction_to_float(stream.get("r_frame_rate"))
    source_fps = average or nominal or float(DEFAULT_FPS)

    # Use standard display cadences. This avoids unusual metadata values while
    # preserving the intended motion cadence of normal camera and phone video.
    supported = (15, 20, 24, 25, 30, 48, 50, 60)
    target_fps = min(supported, key=lambda candidate: abs(candidate - source_fps))
    return source_fps, target_fps


def media_duration(path: Path, fps: int) -> tuple[float, int]:
    """Return exact prepared duration from decoded frame count / item FPS."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries",
            "stream=nb_read_frames,avg_frame_rate,r_frame_rate,duration:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not inspect video duration: {path}\n{result.stderr.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse video duration: {path}") from exc

    streams = payload.get("streams") or []
    stream = streams[0] if streams else {}

    try:
        frames = int(stream.get("nb_read_frames"))
    except (TypeError, ValueError):
        frames = 0

    if frames > 0:
        return frames / fps, frames

    candidates: list[object] = [
        stream.get("duration"),
        (payload.get("format") or {}).get("duration"),
    ]
    for candidate in candidates:
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration, max(1, round(duration * fps))

    raise RuntimeError(f"Could not determine video duration: {path}")

def automatic_video_delay(content_duration: float) -> float:
    """Return the calibrated TRYX display-start latency.

    Video length is represented exactly by decoded frame count / that item's FPS.
    The extra 1.30 seconds is device startup latency after command 402, not part
    of the video duration, so it must not grow with clip length.
    """
    del content_duration
    return DISPLAY_START_LATENCY


def prepare_items(data: dict[str, Any], work_dir: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    items = list(data.get("items", []))
    if not items:
        raise RuntimeError("The playlist is empty")

    log(f"Preparing {len(items)} playlist item(s). The current LCD media remains active.")
    for index, item in enumerate(items, start=1):
        source = Path(str(item["source"])).expanduser().resolve()
        kind = str(item["kind"])
        if not source.is_file():
            raise RuntimeError(f"Playlist file was not found: {source}")

        log(f"Preparing {index}/{len(items)}: {source.name}")
        if kind == "image":
            destination = work_dir / f"{index:03d}-image.png"
            run_command([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-frames:v", "1",
                "-vf", make_filter(item, video=False), str(destination),
            ])
            prepared.append({
                "source": source,
                "kind": kind,
                "prepared": destination,
            })
        elif kind == "video":
            destination = work_dir / f"{index:03d}-video.h264"
            detected_fps, target_fps = source_frame_rate(source)
            keyint = max(1, target_fps)
            min_keyint = max(1, round(target_fps / 10))
            x264_parameters = ":".join([
                "ref=3", f"keyint={keyint}", f"min-keyint={min_keyint}",
                "scenecut=40", "bframes=0", "b-pyramid=0", "weightp=0",
                "aud=1", "repeat-headers=1", "annexb=1", "open-gop=0",
                "fullrange=on",
            ])
            log(
                f"Source cadence: {detected_fps:.3f} FPS; "
                f"encoding this item at {target_fps} FPS"
            )
            run_command([
                "ffmpeg", "-hide_banner", "-y", "-i", str(source),
                "-map", "0:v:0", "-an",
                "-vf", make_filter(item, video=True, target_fps=target_fps),
                "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "fast",
                "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", "9000k",
                "-profile:v", "main", "-level:v", "4.1",
                "-pix_fmt", "yuv420p", "-color_range", "pc",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-colorspace", "bt709", "-x264-params", x264_parameters,
                "-f", "h264", str(destination),
            ])
            content_duration, content_frame_count = media_duration(
                destination, target_fps
            )
            automatic_delay = automatic_video_delay(content_duration)
            log(
                f"Prepared video: {content_frame_count} exact frames at "
                f"{target_fps} FPS = {content_duration:.3f}s; fixed "
                f"display-start sync={automatic_delay:.3f}s"
            )
            prepared.append({
                "source": source,
                "kind": kind,
                "prepared": destination,
                "duration": content_duration,
                "frame_count": content_frame_count,
                "fps": target_fps,
                "source_fps": detected_fps,
                "automatic_delay": automatic_delay,
            })
        else:
            raise RuntimeError(f"Unsupported playlist media type: {kind}")
    return prepared


def preload_items(prepared: list[dict[str, Any]]) -> None:
    log(
        "Preloading every playlist item to the TRYX display. "
        "This happens once so picture and video timing is not extended by later uploads."
    )

    for index, item in enumerate(prepared, start=1):
        source = item["source"]
        log(f"Uploading playlist item {index}/{len(prepared)}: {source.name}")

        if item["kind"] == "image":
            remote_name = run_command_capture(
                [str(VENV_PYTHON), str(IMAGE_SCRIPT), str(item["prepared"])],
                "Remote filename:",
            )
        else:
            remote_name = (
                f"{time.strftime('%Y-%m-%d_%H-%M-%S')}-playlist-{index:03d}."
                f"mp4.h264_{WIDTH}x{HEIGHT}"
            )
            run_command([
                str(VENV_PYTHON), str(VIDEO_UPLOAD_SCRIPT), str(item["prepared"]),
                "--fps", str(int(item["fps"])), "--remote-name", remote_name,
            ])

        item["remote_name"] = remote_name
        log(f"Preloaded as: {remote_name}")

    log("All playlist items are preloaded. Starting timed playback now.")


def activate_preloaded(item: dict[str, Any]) -> float:
    remote_name = str(item["remote_name"])
    log(f"Switching to preloaded media: {remote_name}")
    run_command([str(VENV_PYTHON), str(SELECT_SCRIPT), remote_name])
    # A single proven command-402 restart forces the selected item to begin at
    # frame zero. Images then receive the 2.95-second refresh; videos do not.
    run_command([str(VENV_PYTHON), str(RESTART_SCRIPT)])
    return time.monotonic()


def stop_single_loop() -> None:
    if SINGLE_LOOP_MANAGER.is_file():
        run_command([str(VENV_PYTHON), str(SINGLE_LOOP_MANAGER), "--stop"])


def start_image_refresh() -> subprocess.Popen[str]:
    global _CURRENT_CHILD
    process = subprocess.Popen(
        [str(VENV_PYTHON), str(LOOP_SCRIPT), "--interval", f"{RESTART_INTERVAL:.2f}"],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _CURRENT_CHILD = process
    time.sleep(0.25)
    if process.poll() is not None:
        _CURRENT_CHILD = None
        raise RuntimeError(f"Image refresh loop exited with status {process.returncode}")
    return process


def stop_child(process: subprocess.Popen[str] | None) -> None:
    global _CURRENT_CHILD
    if process is None or process.poll() is not None:
        if _CURRENT_CHILD is process:
            _CURRENT_CHILD = None
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    if _CURRENT_CHILD is process:
        _CURRENT_CHILD = None


def play_image(item: dict[str, Any], seconds: float) -> None:
    log(f"Showing image for {seconds:.0f} seconds: {item['source'].name}")
    started = activate_preloaded(item)

    refresh = start_image_refresh()
    try:
        sleep_until(started + seconds)
    finally:
        stop_child(refresh)


def play_video(item: dict[str, Any], sequence: int) -> None:
    del sequence
    duration = float(item["duration"])
    fps = int(item.get("fps", DEFAULT_FPS))
    frame_count = int(item.get("frame_count", round(duration * fps)))
    automatic_delay = float(
        item.get("automatic_delay", automatic_video_delay(duration))
    )
    log(
        f"Playing complete video: {item['source'].name} "
        f"({frame_count} frames / {fps} FPS = {duration:.3f}s, "
        f"automatic ending delay={automatic_delay:.3f}s)"
    )
    started = activate_preloaded(item)

    # No 2.95-second refresh is sent while a video is active. The exact frame
    # count handles every clip length; the fixed 1.30-second value only accounts
    # for the TRYX display's measured start latency after command 402.
    sleep_until(started + duration + automatic_delay)


def signal_handler(signum: int, frame: object) -> None:
    del signum, frame
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    child = _CURRENT_CHILD
    if child is not None and child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass


def run_playlist(manifest: Path) -> int:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    for required in (VENV_PYTHON, IMAGE_SCRIPT, VIDEO_UPLOAD_SCRIPT, SELECT_SCRIPT, LOOP_SCRIPT, RESTART_SCRIPT):
        if not required.is_file():
            raise RuntimeError(f"Missing proven TRYX file: {required}")

    data = json.loads(manifest.read_text())
    image_seconds = float(data.get("image_seconds", 7))
    if image_seconds not in (5.0, 7.0, 10.0):
        raise RuntimeError("Image duration must be 5, 7, or 10 seconds")
    shuffle = bool(data.get("shuffle", True))
    work_root = CACHE_DIR / "shuffle-work"
    work_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="run-", dir=work_root))

    try:
        prepared = prepare_items(data, work_dir)
        stop_single_loop()
        preload_items(prepared)
        log(
            f"Shuffle ready: {len(prepared)} item(s), images={image_seconds:.0f}s, "
            f"video playback=source-matched FPS, exact frame duration plus {DISPLAY_START_LATENCY:.2f}s display sync, "
            f"random order={'on' if shuffle else 'off'}"
        )

        previous: dict[str, Any] | None = None
        sequence = 0
        while not _STOP_REQUESTED:
            order = list(prepared)
            if shuffle:
                random.shuffle(order)
                if len(order) > 1 and previous is order[0]:
                    order[0], order[1] = order[1], order[0]

            for item in order:
                if _STOP_REQUESTED:
                    break
                sequence += 1
                if item["kind"] == "image":
                    play_image(item, image_seconds)
                else:
                    play_video(item, sequence)
                previous = item
    except InterruptedError:
        log("Shuffle stopped.")
    finally:
        child = _CURRENT_CHILD
        if child is not None:
            stop_child(child)
        shutil.rmtree(work_dir, ignore_errors=True)
        PID_FILE.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an indefinite mixed image/video shuffle on a TRYX display."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--start", action="store_true")
    modes.add_argument("--stop", action="store_true")
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--run", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    try:
        if args.start:
            if args.manifest is None:
                raise RuntimeError("--start requires --manifest")
            return start_playlist(args.manifest.expanduser().resolve())
        if args.stop:
            return stop_playlist()
        if args.status:
            return show_status()
        assert args.run is not None
        return run_playlist(args.run.expanduser().resolve())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
