#!/usr/bin/env python3

from __future__ import annotations

import argparse
import time

import usb.core
import usb.util

from tryx_upload import (
    PID,
    VID,
    drain_input,
    find_bulk_interface,
    make_tryx_packet,
    protobuf_bytes,
    protobuf_varint,
)


DEFAULT_POWER_ON = b"default_poweron_1280x720.mp4.h264"
DEFAULT_STANDBY = b"default_standby_1280x720.mp4.h264"


def build_config_packets(remote_name: str) -> tuple[bytes, bytes]:
    try:
        remote_name_bytes = remote_name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "The remote filename must contain ASCII characters only"
        ) from exc

    # These nested fields reproduce the Windows/Kanali packet:
    #
    # UserConfig field 1:
    #   power-on media filename
    #
    # UserConfig field 2:
    #   enabled = 1
    #   standby media filename
    #
    # UserConfig field 3:
    #   WorkConfig field 3 = selected right-screen media filename
    #
    # UserConfig field 5:
    #   enabled = 1
    #   value = 80
    power_on_config = protobuf_bytes(
        1,
        DEFAULT_POWER_ON,
    )

    standby_config = (
        protobuf_varint(1, 1)
        + protobuf_bytes(2, DEFAULT_STANDBY)
    )

    work_config = protobuf_bytes(
        3,
        remote_name_bytes,
    )

    display_config = (
        protobuf_varint(1, 1)
        + protobuf_varint(2, 80)
    )

    user_config = b"".join([
        protobuf_bytes(1, power_on_config),
        protobuf_bytes(2, standby_config),
        protobuf_bytes(3, work_config),
        protobuf_bytes(5, display_config),
    ])

    # Configuration messages use an empty top-level field 1,
    # unlike the upload messages that contain a session number.
    common_header = protobuf_bytes(1, b"")

    command_200 = make_tryx_packet(
        common_header
        + protobuf_bytes(200, user_config)
    )

    # Empty RunConfigPb is the media-only configuration
    # used in the successful image/video capture.
    command_201 = make_tryx_packet(
        common_header
        + protobuf_bytes(201, b"")
    )

    return command_200, command_201


def write_complete(device, endpoint_address: int, packet: bytes) -> None:
    offset = 0

    while offset < len(packet):
        written = device.write(
            endpoint_address,
            packet[offset:],
            timeout=10000,
        )

        if written <= 0:
            raise IOError(
                f"USB write stopped at {offset}/{len(packet)} bytes"
            )

        offset += written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Select an uploaded media file and apply the "
            "TRYX display configuration."
        )
    )

    parser.add_argument(
        "remote_name",
        help="Exact filename previously uploaded to the TRYX device",
    )

    arguments = parser.parse_args()

    command_200, command_201 = build_config_packets(
        arguments.remote_name
    )

    print(f"Selected media: {arguments.remote_name}")
    print(f"Command 200:    {len(command_200)} bytes")
    print(f"Command 201:    {len(command_201)} bytes")

    device = usb.core.find(
        idVendor=VID,
        idProduct=PID,
    )

    if device is None:
        raise SystemExit(
            f"TRYX device {VID:04x}:{PID:04x} was not found"
        )

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

    print(f"Interface:   {interface_number}")
    print(f"Bulk OUT:    0x{endpoint_out.bEndpointAddress:02x}")
    print(f"Bulk IN:     0x{endpoint_in.bEndpointAddress:02x}")

    try:
        try:
            if device.is_kernel_driver_active(interface_number):
                print("Detaching kernel driver")
                device.detach_kernel_driver(interface_number)
                detached = True

        except (NotImplementedError, usb.core.USBError):
            pass

        try:
            usb.util.claim_interface(
                device,
                interface_number,
            )
            claimed = True

        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 16:
                raise RuntimeError(
                    "The TRYX interface is busy. Run:\n"
                    "sudo systemctl stop ipp-usb.service\n"
                    "sudo pkill -x ipp-usb"
                ) from exc

            raise

        drain_input(device, endpoint_in)

        print("\nSending field 200: select media...")
        write_complete(
            device,
            endpoint_out.bEndpointAddress,
            command_200,
        )
        print("Field 200 sent")

        time.sleep(0.001)

        print("Sending field 201: apply configuration...")
        write_complete(
            device,
            endpoint_out.bEndpointAddress,
            command_201,
        )
        print("Field 201 sent")

        # The Windows capture did not show a TRYX ACK for these
        # two configuration commands.
        time.sleep(0.50)

        print("\nSUCCESS: selection configuration sent.")

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


if __name__ == "__main__":
    main()
