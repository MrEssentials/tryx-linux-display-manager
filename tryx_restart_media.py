#!/usr/bin/env python3

import usb.core
import usb.util

from tryx_upload import (
    DEFAULT_SESSION,
    PID,
    VID,
    find_bulk_interface,
    make_tryx_packet,
    protobuf_bytes,
    protobuf_varint,
    read_tryx_response,
    top_level_fields,
)


def main():
    session_header = protobuf_bytes(
        1,
        protobuf_varint(2, DEFAULT_SESSION),
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

    interface, endpoint_in, endpoint_out = find_bulk_interface(device)
    interface_number = interface.bInterfaceNumber

    detached = False
    claimed = False

    try:
        try:
            if device.is_kernel_driver_active(interface_number):
                device.detach_kernel_driver(interface_number)
                detached = True
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(device, interface_number)
        claimed = True

        print("Sending command 402 to restart current media...")

        written = device.write(
            endpoint_out.bEndpointAddress,
            packet_402,
            timeout=10000,
        )

        print(f"Written: {written}/{len(packet_402)} bytes")

        response = read_tryx_response(
            device,
            endpoint_in,
            timeout_ms=10000,
        )

        fields = top_level_fields(response)

        print(f"Response fields: {fields}")
        print(f"Response hex:    {response.hex()}")

        if 802 not in fields:
            raise RuntimeError(
                f"Expected ACK 802, received {fields}"
            )

        print("Confirmed ACK 802")

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
