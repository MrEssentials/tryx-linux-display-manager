#!/usr/bin/env python3

import argparse
import time

import usb.core
import usb.util

from tryx_upload import (
    DEFAULT_SESSION,
    PID,
    VID,
    drain_input,
    find_bulk_interface,
    make_tryx_packet,
    protobuf_bytes,
    protobuf_varint,
    read_tryx_response,
    top_level_fields,
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Continuously restart the current TRYX media."
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=2.95,
        help="Seconds between restarts; default: 2.95",
    )

    parser.add_argument(
        "--session",
        type=int,
        default=DEFAULT_SESSION,
    )

    return parser.parse_args()


def main():
    arguments = parse_arguments()

    session_header = protobuf_bytes(
        1,
        protobuf_varint(2, arguments.session),
    )

    packet_402 = make_tryx_packet(
        session_header
        + protobuf_bytes(
            402,
            protobuf_bytes(1, b"media"),
        )
    )

    device = usb.core.find(
        idVendor=VID,
        idProduct=PID,
    )

    if device is None:
        raise SystemExit("TRYX device was not found")

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

    print(f"Loop interval: {arguments.interval:.3f} seconds")
    print("Press Ctrl+C to stop looping.")

    try:
        try:
            if device.is_kernel_driver_active(interface_number):
                device.detach_kernel_driver(interface_number)
                detached = True
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(device, interface_number)
        claimed = True

        drain_input(device, endpoint_in)

        count = 0
        next_restart = time.monotonic()

        while True:
            delay = next_restart - time.monotonic()

            if delay > 0:
                time.sleep(delay)

            written = device.write(
                endpoint_out.bEndpointAddress,
                packet_402,
                timeout=10000,
            )

            if written != len(packet_402):
                raise IOError(
                    f"Short write: {written}/{len(packet_402)}"
                )

            response = read_tryx_response(
                device,
                endpoint_in,
                timeout_ms=10000,
            )

            fields = top_level_fields(response)

            if 802 not in fields:
                raise RuntimeError(
                    f"Expected ACK 802, received {fields}"
                )

            count += 1
            print(f"Restart {count}: ACK 802")

            next_restart += arguments.interval

            # Prevent schedule drift after a long USB delay.
            if next_restart < time.monotonic():
                next_restart = (
                    time.monotonic() + arguments.interval
                )

    except KeyboardInterrupt:
        print("\nLoop stopped.")

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
