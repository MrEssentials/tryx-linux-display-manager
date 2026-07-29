#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import usb.core
import usb.util

from tryx_upload import (
    DEFAULT_SESSION,
    HEIGHT,
    PID,
    VID,
    WIDTH,
    drain_input,
    find_bulk_interface,
    make_tryx_packet,
    protobuf_bytes,
    protobuf_varint,
    read_tryx_response,
    top_level_fields,
    validate_h264,
)


MEDIA_CHUNK_SIZE = 256 * 1024


def probe_h264(path: Path) -> tuple[int, int, int]:
    command = [
        "ffprobe",
        "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,nb_read_frames",
        "-of", "default=noprint_wrappers=1",
        str(path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFprobe failed:\n" + result.stderr.strip()
        )

    values: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    try:
        width = int(values["width"])
        height = int(values["height"])
        frame_count = int(values["nb_read_frames"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            "Could not determine H.264 dimensions or frame count.\n"
            + result.stdout
        ) from exc

    return width, height, frame_count


def build_video_packets(
    h264: bytes,
    filename: str,
    session: int,
    fps: int,
    frame_count: int,
) -> tuple[bytes, list[tuple[bytes, int]], bytes, int]:
    session_header = protobuf_bytes(
        1,
        protobuf_varint(2, session),
    )

    header_text = (
        f"Tryx media header v1, fps={fps}, "
        f"size={WIDTH}x{HEIGHT}"
    ).encode("ascii")

    # Video metadata recovered from the Windows capture:
    #
    # field 3 = 4      video
    # field 4 = 1
    # field 5 = FPS
    # field 6 = width
    # field 7 = height
    # field 8 = number of frames
    metadata = b"".join([
        protobuf_varint(1, 0x4D584844),
        protobuf_bytes(2, header_text),
        protobuf_varint(3, 4),
        protobuf_varint(4, 1),
        protobuf_varint(5, fps),
        protobuf_varint(6, WIDTH),
        protobuf_varint(7, HEIGHT),
        protobuf_varint(8, frame_count),
    ])

    media_blob = (
        len(metadata).to_bytes(4, "little")
        + metadata
        + h264
    )

    command_400 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            400,
            protobuf_bytes(
                1,
                filename.encode("ascii"),
            )
            + protobuf_varint(
                2,
                len(media_blob),
            ),
        )
    )

    command_401_packets: list[tuple[bytes, int]] = []

    for offset in range(0, len(media_blob), MEDIA_CHUNK_SIZE):
        chunk = media_blob[offset:offset + MEDIA_CHUNK_SIZE]

        packet = make_tryx_packet(
            session_header
            + protobuf_bytes(
                401,
                protobuf_bytes(1, chunk),
            )
        )

        command_401_packets.append(
            (packet, len(chunk))
        )

    command_402 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            402,
            protobuf_bytes(1, b"media"),
        )
    )

    return (
        command_400,
        command_401_packets,
        command_402,
        len(media_blob),
    )


def write_all(
    device,
    endpoint_address: int,
    packet: bytes,
) -> None:
    offset = 0

    while offset < len(packet):
        written = device.write(
            endpoint_address,
            packet[offset:],
            timeout=30000,
        )

        if written <= 0:
            raise IOError(
                "USB write made no progress at "
                f"{offset}/{len(packet)} bytes"
            )

        offset += written

        if len(packet) >= 65536:
            print(
                f"    USB: {offset}/{len(packet)} bytes "
                f"({offset * 100 / len(packet):.1f}%)"
            )

    if offset != len(packet):
        raise IOError(
            f"USB wrote {offset} of {len(packet)} bytes"
        )


def send_and_expect(
    device,
    endpoint_out,
    endpoint_in,
    packet: bytes,
    label: str,
    expected_ack: int,
) -> None:
    print(f"\nSending {label}: {len(packet)} bytes")

    write_all(
        device,
        endpoint_out.bEndpointAddress,
        packet,
    )

    response = read_tryx_response(
        device,
        endpoint_in,
        timeout_ms=30000,
    )

    fields = top_level_fields(response)

    print(f"Response fields: {fields}")

    if expected_ack not in fields:
        raise RuntimeError(
            f"{label} expected ACK {expected_ack}, "
            f"but received {fields}\n"
            f"Response: {response.hex()}"
        )

    print(f"Confirmed ACK {expected_ack}")


def upload_video(
    command_400: bytes,
    command_401_packets: list[tuple[bytes, int]],
    command_402: bytes,
) -> None:
    device = usb.core.find(
        idVendor=VID,
        idProduct=PID,
    )

    if device is None:
        raise RuntimeError(
            f"TRYX device {VID:04x}:{PID:04x} was not found"
        )

    print(f"\nFound TRYX device: {VID:04x}:{PID:04x}")

    try:
        device.set_configuration()
    except usb.core.USBError:
        pass

    interface, endpoint_in, endpoint_out = (
        find_bulk_interface(device)
    )

    interface_number = interface.bInterfaceNumber
    detached = False
    claimed = False

    print(f"Interface: {interface_number}")
    print(f"Bulk OUT:  0x{endpoint_out.bEndpointAddress:02x}")
    print(f"Bulk IN:   0x{endpoint_in.bEndpointAddress:02x}")

    try:
        try:
            if device.is_kernel_driver_active(interface_number):
                print("Detaching kernel driver")
                device.detach_kernel_driver(interface_number)
                detached = True
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(
            device,
            interface_number,
        )
        claimed = True

        drain_input(device, endpoint_in)

        send_and_expect(
            device,
            endpoint_out,
            endpoint_in,
            command_400,
            "command 400: announce video",
            800,
        )

        time.sleep(0.05)

        total_chunks = len(command_401_packets)

        for index, (packet, media_size) in enumerate(
            command_401_packets,
            start=1,
        ):
            print(
                f"\nMedia chunk {index}/{total_chunks}: "
                f"{media_size} media bytes"
            )

            send_and_expect(
                device,
                endpoint_out,
                endpoint_in,
                packet,
                f"command 401 chunk {index}/{total_chunks}",
                801,
            )

            time.sleep(0.02)

        send_and_expect(
            device,
            endpoint_out,
            endpoint_in,
            command_402,
            "command 402: activate video",
            802,
        )

    finally:
        if claimed:
            try:
                usb.util.release_interface(
                    device,
                    interface_number,
                )
            except usb.core.USBError:
                pass

        if detached:
            try:
                device.attach_kernel_driver(
                    interface_number
                )
            except usb.core.USBError:
                pass

        usb.util.dispose_resources(device)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upload a raw Annex-B H.264 video to a "
            "TRYX Turris display."
        )
    )

    parser.add_argument(
        "h264",
        type=Path,
        help="Raw Annex-B H.264 file",
    )

    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Playback frame rate; default: 30",
    )

    parser.add_argument(
        "--session",
        type=int,
        default=DEFAULT_SESSION,
    )

    parser.add_argument(
        "--remote-name",
        help="Optional remote TRYX filename",
    )

    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    source = arguments.h264.expanduser().resolve()

    if not source.is_file():
        raise SystemExit(f"File was not found: {source}")

    h264 = source.read_bytes()

    if not h264:
        raise SystemExit("The H.264 file is empty")

    nal_types = validate_h264(h264)
    nal_counts = Counter(nal_types)

    width, height, frame_count = probe_h264(source)

    if (width, height) != (WIDTH, HEIGHT):
        raise SystemExit(
            f"Video is {width}x{height}; expected "
            f"{WIDTH}x{HEIGHT}"
        )

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S-%f"
    )[:-3]

    remote_name = arguments.remote_name or (
        f"{timestamp}.mp4.h264_{WIDTH}x{HEIGHT}"
    )

    (
        command_400,
        command_401_packets,
        command_402,
        media_blob_size,
    ) = build_video_packets(
        h264=h264,
        filename=remote_name,
        session=arguments.session,
        fps=arguments.fps,
        frame_count=frame_count,
    )

    print(f"Source:          {source}")
    print(f"Remote filename: {remote_name}")
    print(f"Resolution:      {width}x{height}")
    print(f"FPS metadata:    {arguments.fps}")
    print(f"Frame count:     {frame_count}")
    print(f"H.264 size:      {len(h264)} bytes")
    print(f"Media blob:      {media_blob_size} bytes")
    print(f"NAL counts:      {dict(sorted(nal_counts.items()))}")
    print(f"Media chunks:    {len(command_401_packets)}")
    print(
        "Chunk sizes:     "
        + ", ".join(
            str(media_size)
            for _, media_size in command_401_packets
        )
    )

    upload_video(
        command_400,
        command_401_packets,
        command_402,
    )

    print("\nSUCCESS: video uploaded and activated.")


if __name__ == "__main__":
    main()
