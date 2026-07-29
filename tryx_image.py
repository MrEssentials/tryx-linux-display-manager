#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tryx_upload import (
    DEFAULT_SESSION,
    build_packets,
    encode_image,
    make_remote_filename,
    upload_packets,
    validate_h264,
)


APP_DIR = Path(__file__).resolve().parent
SELECT_SCRIPT = APP_DIR / "tryx_select_media.py"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert, upload, and save an image as the active "
            "TRYX display media."
        )
    )

    parser.add_argument(
        "image",
        type=Path,
        help="PNG, JPG, WEBP, or another FFmpeg-readable image",
    )

    parser.add_argument(
        "--session",
        type=int,
        default=DEFAULT_SESSION,
    )

    parser.add_argument(
        "--save-h264",
        type=Path,
        help="Optionally save the generated H.264 keyframe",
    )

    parser.add_argument(
        "--save-delay",
        type=float,
        default=0.5,
        help="Seconds to wait before saving the active image",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    source = args.image.expanduser().resolve()

    if not source.is_file():
        raise SystemExit(f"Image was not found: {source}")

    if not SELECT_SCRIPT.is_file():
        raise SystemExit(f"Selector script is missing: {SELECT_SCRIPT}")

    remote_name = make_remote_filename(source)

    print(f"Source image:    {source}")
    print(f"Remote filename: {remote_name}")

    with tempfile.TemporaryDirectory(
        prefix="tryx-image-"
    ) as temporary_directory:
        h264_path = Path(temporary_directory) / "image.h264"

        encode_image(source, h264_path)

        h264 = h264_path.read_bytes()
        nal_types = validate_h264(h264)

        print(f"H.264 size:      {len(h264)} bytes")
        print(f"NAL types:       {nal_types}")

        if args.save_h264 is not None:
            save_path = args.save_h264.expanduser().resolve()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(h264_path, save_path)
            print(f"Saved H.264:     {save_path}")

        packet_400, packet_401, packet_402 = build_packets(
            h264,
            remote_name,
            args.session,
        )

        upload_packets(
            packet_400,
            packet_401,
            packet_402,
        )

        print(
            f"\nImage active. Waiting {args.save_delay:.3f} "
            "seconds before saving..."
        )
        time.sleep(args.save_delay)

        subprocess.run(
            [
                sys.executable,
                str(SELECT_SCRIPT),
                remote_name,
            ],
            check=True,
        )

        print("\nSUCCESS: image uploaded and saved as active media.")


if __name__ == "__main__":
    main()
