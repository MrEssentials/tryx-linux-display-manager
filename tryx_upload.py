#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import usb.core
import usb.util


VID = 0x391A
PID = 0x2011

WIDTH = 1280
HEIGHT = 720
FPS = 30

# This session value is already proven to work with the device.
DEFAULT_SESSION = 981521


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Varint cannot be negative")

    result = bytearray()

    while True:
        byte = value & 0x7F
        value >>= 7

        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def protobuf_varint(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def protobuf_bytes(field: int, value: bytes) -> bytes:
    return (
        varint((field << 3) | 2)
        + varint(len(value))
        + value
    )


def make_tryx_packet(body: bytes) -> bytes:
    return (
        b"TRYX"
        + len(body).to_bytes(4, "little")
        + body
    )


def build_packets(
    h264: bytes,
    filename: str,
    session: int,
) -> tuple[bytes, bytes, bytes]:
    session_header = protobuf_bytes(
        1,
        protobuf_varint(2, session),
    )

    media_header_text = (
        f"Tryx media header v1, fps={FPS}, "
        f"size={WIDTH}x{HEIGHT}"
    ).encode("ascii")

    media_metadata = b"".join([
        protobuf_varint(1, 0x4D584844),
        protobuf_bytes(2, media_header_text),
        protobuf_varint(3, 2),
        protobuf_varint(4, 1),
        protobuf_varint(5, FPS),
        protobuf_varint(6, WIDTH),
        protobuf_varint(7, HEIGHT),
        protobuf_varint(8, 1),
    ])

    media_blob = (
        len(media_metadata).to_bytes(4, "little")
        + media_metadata
        + h264
    )

    command_400 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            400,
            protobuf_bytes(1, filename.encode("ascii"))
            + protobuf_varint(2, len(media_blob)),
        )
    )

    command_401 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            401,
            protobuf_bytes(1, media_blob),
        )
    )

    command_402 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            402,
            protobuf_bytes(1, b"media"),
        )
    )

    return command_400, command_401, command_402


def encode_image(source: Path, destination: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")

    if ffmpeg is None:
        raise RuntimeError(
            "FFmpeg was not found. Install it with: "
            "sudo apt install ffmpeg"
        )

    # Preserve the complete image without stretching it. Images with a
    # different aspect ratio receive black padding.
    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,"
        "format=yuv420p"
    )

    x264_options = ":".join([
        "keyint=30",
        "min-keyint=30",
        "scenecut=0",
        "bframes=0",
        "aud=1",
        "repeat-headers=1",
        "fullrange=on",
    ])

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-an",
        "-vf", video_filter,
        "-frames:v", "1",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-profile:v", "main",
        "-level:v", "4.0",
        "-preset", "medium",
        "-crf", "18",
        "-color_range", "pc",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-x264-params", x264_options,
        "-f", "h264",
        str(destination),
    ]

    print("Encoding image as one H.264 keyframe...")

    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg failed:\n"
            + result.stderr.strip()
        )

    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("FFmpeg produced an empty H.264 file")


def scan_nal_types(data: bytes) -> list[int]:
    types: list[int] = []
    position = 0

    while position < len(data) - 3:
        if data[position:position + 4] == b"\x00\x00\x00\x01":
            header_position = position + 4
            position = header_position

        elif data[position:position + 3] == b"\x00\x00\x01":
            header_position = position + 3
            position = header_position

        else:
            position += 1
            continue

        if header_position < len(data):
            types.append(data[header_position] & 0x1F)

    return types


def validate_h264(data: bytes) -> list[int]:
    nal_types = scan_nal_types(data)
    required = {5, 7, 8}
    missing = required.difference(nal_types)

    if missing:
        raise RuntimeError(
            "Generated H.264 is missing required NAL types: "
            + ", ".join(str(value) for value in sorted(missing))
        )

    return nal_types


def read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    shift = 0

    while position < len(data):
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift

        if not byte & 0x80:
            return value, position

        shift += 7

        if shift >= 70:
            raise ValueError("Invalid protobuf varint")

    raise ValueError("Truncated protobuf varint")


def top_level_fields(packet: bytes) -> list[int]:
    if len(packet) < 8 or packet[:4] != b"TRYX":
        return []

    declared = int.from_bytes(packet[4:8], "little")
    body = packet[8:8 + declared]

    fields: list[int] = []
    position = 0

    while position < len(body):
        tag, position = read_varint(body, position)
        field = tag >> 3
        wire_type = tag & 7

        fields.append(field)

        if wire_type == 0:
            _, position = read_varint(body, position)

        elif wire_type == 1:
            position += 8

        elif wire_type == 2:
            length, position = read_varint(body, position)
            position += length

        elif wire_type == 5:
            position += 4

        else:
            break

    return fields


def find_bulk_interface(device):
    configuration = device.get_active_configuration()

    for interface in configuration:
        endpoint_in = None
        endpoint_out = None

        for endpoint in interface:
            endpoint_type = usb.util.endpoint_type(
                endpoint.bmAttributes
            )

            if endpoint_type != usb.util.ENDPOINT_TYPE_BULK:
                continue

            direction = usb.util.endpoint_direction(
                endpoint.bEndpointAddress
            )

            if direction == usb.util.ENDPOINT_IN:
                endpoint_in = endpoint
            else:
                endpoint_out = endpoint

        if endpoint_in is not None and endpoint_out is not None:
            return interface, endpoint_in, endpoint_out

    raise RuntimeError(
        "No interface containing bulk IN and OUT endpoints was found"
    )


def drain_input(device, endpoint) -> None:
    while True:
        try:
            fragment = bytes(
                device.read(
                    endpoint.bEndpointAddress,
                    65536,
                    timeout=50,
                )
            )

            if fragment:
                print(
                    f"Discarded {len(fragment)} stale bytes: "
                    f"{fragment[:32].hex()}"
                )

        except usb.core.USBTimeoutError:
            return

        except usb.core.USBError:
            return


def read_tryx_response(
    device,
    endpoint,
    timeout_ms: int = 10000,
) -> bytes:
    buffer = bytearray()
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        remaining = max(
            1,
            int((deadline - time.monotonic()) * 1000),
        )

        try:
            fragment = bytes(
                device.read(
                    endpoint.bEndpointAddress,
                    65536,
                    timeout=min(1000, remaining),
                )
            )

        except usb.core.USBTimeoutError:
            continue

        if not fragment:
            continue

        print(f"Received USB fragment: {len(fragment)} bytes")
        buffer.extend(fragment)

        magic_position = buffer.find(b"TRYX")

        if magic_position < 0:
            if len(buffer) > 3:
                del buffer[:-3]
            continue

        if magic_position:
            del buffer[:magic_position]

        if len(buffer) < 8:
            continue

        body_length = int.from_bytes(buffer[4:8], "little")
        complete_length = 8 + body_length

        if len(buffer) >= complete_length:
            return bytes(buffer[:complete_length])

    raise TimeoutError(
        "Timed out waiting for a complete TRYX response"
    )


def write_all(
    device,
    endpoint,
    data: bytes,
    timeout_ms: int = 30000,
) -> int:
    """Write an entire TRYX message using USB-safe chunks."""

    max_packet = int(endpoint.wMaxPacketSize) & 0x7FF

    if max_packet <= 0:
        max_packet = 64

    # Keep every intermediate transfer aligned to the USB endpoint's
    # maximum packet size. Only the final transfer may be shorter.
    requested_chunk_size = 64 * 1024
    chunk_size = (
        requested_chunk_size // max_packet
    ) * max_packet

    if chunk_size <= 0:
        chunk_size = max_packet

    total_written = 0
    total_length = len(data)

    while total_written < total_length:
        chunk_end = min(
            total_written + chunk_size,
            total_length,
        )

        chunk = data[total_written:chunk_end]
        chunk_written = 0

        while chunk_written < len(chunk):
            written = device.write(
                endpoint.bEndpointAddress,
                chunk[chunk_written:],
                timeout=timeout_ms,
            )

            written = int(written)

            if written <= 0:
                raise IOError(
                    "USB write made no forward progress"
                )

            chunk_written += written
            total_written += written

        percent = total_written * 100 / total_length

        print(
            f"\rUSB progress: {total_written}/{total_length} "
            f"bytes ({percent:.1f}%)",
            end="",
            flush=True,
        )

        # Gives the device firmware a small amount of processing time.
        time.sleep(0.002)

    print()
    return total_written


def send_and_expect_ack(
    device,
    endpoint_out,
    endpoint_in,
    packet: bytes,
    command: int,
    expected_ack: int,
) -> None:
    print(
        f"\nSending command {command}: "
        f"{len(packet)} bytes"
    )

    written = write_all(
        device,
        endpoint_out,
        packet,
        timeout_ms=30000,
    )

    print(f"Written: {written} bytes")

    response = read_tryx_response(
        device,
        endpoint_in,
        timeout_ms=20000,
    )

    fields = top_level_fields(response)

    print(f"Response fields: {fields}")
    print(f"Response hex:    {response.hex()}")

    if expected_ack not in fields:
        raise RuntimeError(
            f"Command {command} expected ACK {expected_ack}, "
            f"but received fields {fields}"
        )

    print(f"Confirmed ACK {expected_ack}")


def upload_packets(
    packet_400: bytes,
    packet_401: bytes,
    packet_402: bytes,
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

    interface, endpoint_in, endpoint_out = find_bulk_interface(
        device
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

        usb.util.claim_interface(device, interface_number)
        claimed = True

        drain_input(device, endpoint_in)

        send_and_expect_ack(
            device,
            endpoint_out,
            endpoint_in,
            packet_400,
            command=400,
            expected_ack=800,
        )

        time.sleep(0.10)

        send_and_expect_ack(
            device,
            endpoint_out,
            endpoint_in,
            packet_401,
            command=401,
            expected_ack=801,
        )

        time.sleep(0.10)

        send_and_expect_ack(
            device,
            endpoint_out,
            endpoint_in,
            packet_402,
            command=402,
            expected_ack=802,
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
                device.attach_kernel_driver(interface_number)
            except usb.core.USBError:
                pass

        usb.util.dispose_resources(device)


def make_remote_filename(source: Path) -> str:
    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S-%f"
    )[:-3]

    extension = source.suffix.lower()

    if not extension:
        extension = ".png"

    return (
        f"{timestamp}{extension}"
        f".h264_{WIDTH}x{HEIGHT}"
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an image to H.264 and upload it to a "
            "TRYX Turris display."
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
        help=f"TRYX session ID; default: {DEFAULT_SESSION}",
    )

    parser.add_argument(
        "--save-h264",
        type=Path,
        help="Optionally save the generated H.264 file here",
    )

    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build and validate the media without using USB",
    )

    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    source = arguments.image.expanduser().resolve()

    if not source.is_file():
        raise SystemExit(f"Image was not found: {source}")

    remote_filename = make_remote_filename(source)

    with tempfile.TemporaryDirectory(
        prefix="tryx-upload-"
    ) as temporary_directory:
        h264_path = Path(temporary_directory) / "image.h264"

        print(f"Source image:    {source}")
        print(f"Remote filename: {remote_filename}")
        print(f"Resolution:      {WIDTH}x{HEIGHT}")
        print(f"Frame rate:      {FPS}")
        print(f"Session:         {arguments.session}")

        encode_image(source, h264_path)

        h264 = h264_path.read_bytes()
        nal_types = validate_h264(h264)

        print(f"H.264 size:      {len(h264)} bytes")
        print(f"NAL types:       {nal_types}")

        if arguments.save_h264 is not None:
            save_path = arguments.save_h264.expanduser().resolve()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(h264)
            print(f"Saved H.264:     {save_path}")

        packet_400, packet_401, packet_402 = build_packets(
            h264,
            remote_filename,
            arguments.session,
        )

        print(f"Command 400:     {len(packet_400)} bytes")
        print(f"Command 401:     {len(packet_401)} bytes")
        print(f"Command 402:     {len(packet_402)} bytes")

        if arguments.build_only:
            print("\nBuild-only validation succeeded.")
            return

        upload_packets(
            packet_400,
            packet_401,
            packet_402,
        )

        print("\nSUCCESS: custom image uploaded and activated.")


if __name__ == "__main__":
    main()
