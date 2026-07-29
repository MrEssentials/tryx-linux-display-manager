#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


VID = 0x391A
PID = 0x2011

APP_DIR = Path(__file__).resolve().parent
IMAGE_SCRIPT = APP_DIR / "tryx_image.py"
VIDEO_SCRIPT = APP_DIR / "tryx_play.py"

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".gif",
    ".h264",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tryx",
        description=(
            "Upload images or videos to a TRYX Turris display."
        ),
    )

    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        help="Image or video to display",
    )

    parser.add_argument(
        "--kind",
        choices=("image", "video"),
        help="Override automatic media-type detection",
    )

    parser.add_argument(
        "--fit",
        choices=("pad", "crop", "stretch"),
        default="pad",
        help=(
            "Video fitting mode: pad, crop, or stretch. "
            "Default: pad"
        ),
    )

    parser.add_argument(
        "--duration",
        type=float,
        help="Limit video conversion to this many seconds",
    )

    parser.add_argument(
        "--save-h264",
        type=Path,
        help="Keep a copy of the generated H.264 stream",
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check software, scripts, USB device, and permissions",
    )

    return parser.parse_args()


def print_check(label: str, passed: bool, detail: str) -> None:
    mark = "OK" if passed else "FAIL"
    print(f"[{mark:<4}] {label}: {detail}")


def run_doctor() -> int:
    success = True

    print("TRYX Linux diagnostic\n")

    for program in ("ffmpeg", "ffprobe"):
        location = shutil.which(program)
        passed = location is not None
        success &= passed

        print_check(
            program,
            passed,
            location or "not installed",
        )

    for name, path in (
        ("Image uploader", IMAGE_SCRIPT),
        ("Video player", VIDEO_SCRIPT),
    ):
        passed = path.is_file()
        success &= passed

        print_check(
            name,
            passed,
            str(path),
        )

    try:
        import usb.core  # type: ignore[import-not-found]

        print_check(
            "PyUSB",
            True,
            "installed",
        )

        device = usb.core.find(
            idVendor=VID,
            idProduct=PID,
        )

        passed = device is not None
        success &= passed

        print_check(
            "TRYX USB device",
            passed,
            (
                f"found at {VID:04x}:{PID:04x}"
                if passed
                else f"{VID:04x}:{PID:04x} not found"
            ),
        )

        if device is not None:
            try:
                configuration = device.get_active_configuration()

                print_check(
                    "USB permissions",
                    True,
                    (
                        "device is accessible; "
                        f"configuration {configuration.bConfigurationValue}"
                    ),
                )

            except Exception as exc:
                success = False

                print_check(
                    "USB permissions",
                    False,
                    str(exc),
                )

    except ImportError as exc:
        success = False

        print_check(
            "PyUSB",
            False,
            str(exc),
        )

    print()

    if success:
        print("All checks passed.")
        return 0

    print("One or more checks failed.")
    return 1


def detect_kind(source: Path) -> str:
    extension = source.suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        return "image"

    if extension in VIDEO_EXTENSIONS:
        return "video"

    file_program = shutil.which("file")

    if file_program is not None:
        result = subprocess.run(
            [
                file_program,
                "--brief",
                "--mime-type",
                str(source),
            ],
            capture_output=True,
            text=True,
        )

        mime_type = result.stdout.strip().lower()

        if mime_type.startswith("image/"):
            return "image"

        if mime_type.startswith("video/"):
            return "video"

    raise RuntimeError(
        "Could not determine whether this is an image or video. "
        "Use --kind image or --kind video."
    )


def run_command(command: list[str]) -> int:
    print("Media command:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    print()

    result = subprocess.run(command)
    return result.returncode


def main() -> int:
    arguments = parse_arguments()

    if arguments.doctor:
        return run_doctor()

    if arguments.media is None:
        print(
            "A media file is required.\n"
            "Example: tryx ~/Pictures/photo.jpg",
            file=sys.stderr,
        )
        return 2

    source = arguments.media.expanduser().resolve()

    if not source.is_file():
        print(
            f"Media file was not found: {source}",
            file=sys.stderr,
        )
        return 2

    try:
        kind = arguments.kind or detect_kind(source)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Source: {source}")
    print(f"Type:   {kind}")
    print()

    if kind == "image":
        if not IMAGE_SCRIPT.is_file():
            print(
                f"Missing image uploader: {IMAGE_SCRIPT}",
                file=sys.stderr,
            )
            return 1

        command = [
            sys.executable,
            str(IMAGE_SCRIPT),
            str(source),
        ]

        if arguments.save_h264 is not None:
            command.extend([
                "--save-h264",
                str(arguments.save_h264.expanduser().resolve()),
            ])

    else:
        if not VIDEO_SCRIPT.is_file():
            print(
                f"Missing video player: {VIDEO_SCRIPT}",
                file=sys.stderr,
            )
            return 1

        command = [
            sys.executable,
            str(VIDEO_SCRIPT),
            str(source),
            "--fit",
            arguments.fit,
        ]

        if arguments.duration is not None:
            command.extend([
                "--duration",
                str(arguments.duration),
            ])

        if arguments.save_h264 is not None:
            command.extend([
                "--save-h264",
                str(arguments.save_h264.expanduser().resolve()),
            ])

    return run_command(command)


if __name__ == "__main__":
    raise SystemExit(main())
