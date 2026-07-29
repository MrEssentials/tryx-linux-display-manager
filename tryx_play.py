#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


WIDTH = 1280
HEIGHT = 720
FPS = 60
BITRATE = "12000k"

APP_DIR = Path(__file__).resolve().parent
UPLOAD_SCRIPT = APP_DIR / "tryx_video_upload.py"
SELECT_SCRIPT = APP_DIR / "tryx_select_media.py"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a video, upload it to a TRYX Turris display, "
            "and save it for native looping."
        )
    )

    parser.add_argument(
        "video",
        type=Path,
        help="MP4, MOV, WebM, MKV, GIF, or another FFmpeg-readable video",
    )

    parser.add_argument(
        "--fit",
        choices=("pad", "crop", "stretch"),
        default="pad",
        help="Fit mode for 1280x720; default: pad",
    )

    parser.add_argument(
        "--save-delay",
        type=float,
        default=2.8,
        help=(
            "Seconds to wait after playback starts before saving "
            "the configuration; default: 2.8"
        ),
    )

    parser.add_argument(
        "--duration",
        type=float,
        help="Optionally limit the converted video length in seconds",
    )

    parser.add_argument(
        "--remote-name",
        help="Optional exact filename stored on the TRYX display",
    )

    parser.add_argument(
        "--save-h264",
        type=Path,
        help="Optionally keep a copy of the converted H.264 stream",
    )

    parser.add_argument(
        "--encode-only",
        action="store_true",
        help="Convert and validate without uploading",
    )

    return parser.parse_args()


def require_program(name: str) -> str:
    path = shutil.which(name)

    if path is None:
        raise RuntimeError(
            f"{name} was not found in PATH"
        )

    return path


def make_filter(fit: str) -> str:
    if fit == "pad":
        resize = (
            f"scale={WIDTH}:{HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2"
        )

    elif fit == "crop":
        resize = (
            f"scale={WIDTH}:{HEIGHT}:"
            "force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}"
        )

    else:
        resize = f"scale={WIDTH}:{HEIGHT}"

    return (
        f"{resize},"
        "setsar=1,"
        f"fps={FPS},"
        "format=yuv420p"
    )


def make_remote_name() -> str:
    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S-%f"
    )[:-3]

    return (
        f"{timestamp}.mp4"
        f".h264_{WIDTH}x{HEIGHT}"
    )


def encode_video(
    source: Path,
    destination: Path,
    fit: str,
    duration: float | None,
) -> None:
    ffmpeg = require_program("ffmpeg")

    x264_parameters = ":".join([
        "ref=3",
        "keyint=60",
        "min-keyint=6",
        "scenecut=40",
        "bframes=0",
        "b-pyramid=0",
        "weightp=0",
        "aud=1",
        "repeat-headers=1",
        "annexb=1",
        "open-gop=0",
        "fullrange=on",
    ])

    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i", str(source),
    ]

    if duration is not None:
        command.extend([
            "-t", str(duration),
        ])

    command.extend([
        "-map", "0:v:0",
        "-an",
        "-vf", make_filter(fit),
        "-fps_mode", "cfr",
        "-c:v", "libx264",
        "-preset", "fast",
        "-b:v", BITRATE,
        "-profile:v", "main",
        "-level:v", "4.1",
        "-pix_fmt", "yuv420p",
        "-color_range", "pc",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-x264-params", x264_parameters,
        "-f", "h264",
        str(destination),
    ])

    print("\nEncoding with the proven Kanali-compatible settings...")
    print(f"Resolution: {WIDTH}x{HEIGHT}")
    print(f"Frame rate: {FPS}")
    print(f"Bitrate:    {BITRATE}")
    print(f"Fit mode:   {fit}")

    subprocess.run(
        command,
        check=True,
    )

    if not destination.is_file():
        raise RuntimeError(
            "FFmpeg did not produce an H.264 file"
        )

    if destination.stat().st_size == 0:
        raise RuntimeError(
            "FFmpeg produced an empty H.264 file"
        )


def inspect_h264(path: Path) -> None:
    ffprobe = require_program("ffprobe")

    command = [
        ffprobe,
        "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries",
        (
            "stream=codec_name,profile,level,width,height,"
            "pix_fmt,color_range,color_space,nb_read_frames"
        ),
        "-of", "default=noprint_wrappers=1",
        str(path),
    ]

    result = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
    )

    print("\nConverted stream:")
    print(result.stdout.strip())

    required_values = {
        "codec_name": "h264",
        "profile": "Main",
        "level": "41",
        "width": str(WIDTH),
        "height": str(HEIGHT),
    }

    found: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            found[key] = value

    problems: list[str] = []

    for key, expected in required_values.items():
        actual = found.get(key)

        if actual != expected:
            problems.append(
                f"{key}: expected {expected!r}, got {actual!r}"
            )

    if problems:
        raise RuntimeError(
            "Converted stream does not match the proven format:\n"
            + "\n".join(problems)
        )


def check_decode(path: Path) -> None:
    ffmpeg = require_program("ffmpeg")

    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-v", "error",
            "-i", str(path),
            "-f", "null",
            "-",
        ],
        text=True,
        capture_output=True,
    )

    if result.returncode != 0 or result.stderr.strip():
        raise RuntimeError(
            "The generated H.264 stream has decode errors:\n"
            + result.stderr.strip()
        )

    print("Decode test: passed with no errors")


def run_script(arguments: list[str]) -> None:
    print()
    print("$", " ".join(arguments))

    subprocess.run(
        arguments,
        check=True,
    )


def main() -> None:
    args = parse_arguments()

    source = args.video.expanduser().resolve()

    if not source.is_file():
        raise SystemExit(
            f"Video was not found: {source}"
        )

    if args.save_delay < 0:
        raise SystemExit(
            "--save-delay cannot be negative"
        )

    if args.duration is not None and args.duration <= 0:
        raise SystemExit(
            "--duration must be greater than zero"
        )

    if not UPLOAD_SCRIPT.is_file():
        raise SystemExit(
            f"Missing upload script: {UPLOAD_SCRIPT}"
        )

    if not SELECT_SCRIPT.is_file():
        raise SystemExit(
            f"Missing selector script: {SELECT_SCRIPT}"
        )

    remote_name = (
        args.remote_name
        if args.remote_name
        else make_remote_name()
    )

    print(f"Source video: {source}")
    print(f"Remote name:  {remote_name}")

    with tempfile.TemporaryDirectory(
        prefix="tryx-native-play-"
    ) as temporary_directory:
        h264_path = (
            Path(temporary_directory)
            / "converted.h264"
        )

        encode_video(
            source=source,
            destination=h264_path,
            fit=args.fit,
            duration=args.duration,
        )

        inspect_h264(h264_path)
        check_decode(h264_path)

        print(
            f"H.264 size: {h264_path.stat().st_size:,} bytes"
        )

        if args.save_h264 is not None:
            save_path = (
                args.save_h264
                .expanduser()
                .resolve()
            )

            save_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(
                h264_path,
                save_path,
            )

            print(f"Saved H.264: {save_path}")

        if args.encode_only:
            print("\nEncode-only test completed successfully.")
            return

        run_script([
            sys.executable,
            str(UPLOAD_SCRIPT),
            str(h264_path),
            "--fps", str(FPS),
            "--remote-name", remote_name,
        ])

        print()
        print(
            f"Playback started. Waiting "
            f"{args.save_delay:.3f} seconds before saving..."
        )

        time.sleep(args.save_delay)

        run_script([
            sys.executable,
            str(SELECT_SCRIPT),
            remote_name,
        ])

        print()
        print("SUCCESS")
        print("Video uploaded, selected, and saved for native looping.")
        print("No repeated command 402 process is required.")


if __name__ == "__main__":
    main()
