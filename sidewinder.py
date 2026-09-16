#!/usr/bin/env python3
"""Microsoft SideWinder Force Feedback Wheel: read steering position.

This is the 1998 USB wheel: https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel

The kernel binds hid-generic + hid-pidff and exposes an input node, but on the
unit tested the input node never delivered reports. Raw HID reports do arrive,
so this tool reads the wheel straight from hidraw. Force feedback is handled by
native_ff.py, which writes the PID output reports directly.

  read [--raw] [--device PATH]        stream steering position
  watch [--hz N] [--device PATH]      same, but one line per report with buttons

Reading requires permission on the hidraw node. Run as root, or install the
udev rule in README.md.
"""

import argparse
import os
import select
import sys
import time

VENDOR_ID = "045e"
PRODUCT_ID = "0034"

AXIS_MIN = -512
AXIS_MAX = 511
REPORT_ID_STEERING = 0x01
REPORT_SIZE = 7


def norm(value):
    """Map a raw steering value to -1.0 (full left) .. +1.0 (full right)."""
    return 2.0 * (value - AXIS_MIN) / (AXIS_MAX - AXIS_MIN) - 1.0


def find_hidraw():
    """Locate the wheel's hidraw node by USB ID (hidraw numbers are not stable)."""
    for entry in sorted(os.listdir("/sys/class/hidraw")):
        base = "/sys/class/hidraw/%s/device" % entry
        try:
            with open(os.path.join(base, "uevent")) as fh:
                uevent = fh.read()
        except OSError:
            continue
        if "HID_ID=" not in uevent:
            continue
        # HID_ID=0003:0000045E:00000034
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                parts = line.split("=", 1)[1].split(":")
                if len(parts) == 3 and parts[1].endswith(VENDOR_ID.upper()) \
                        and parts[2].endswith(PRODUCT_ID.upper()):
                    return "/dev/" + entry
    return None


def open_hidraw(path):
    if path is None:
        path = find_hidraw()
    if path is None:
        sys.exit("could not find the wheel's hidraw node; pass --device")
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK), path
    except PermissionError:
        sys.exit("permission denied on %s\n"
                 "run as root, or install the udev rule described in README.md" % path)
    except FileNotFoundError:
        sys.exit("no such device: %s" % path)


def decode_steering_report(data):
    """Decode report ID 1 into (x, y, rz, buttons), or None if not that report.

    Layout (7 bytes):
      [0] report ID (0x01)
      [1] steering low 8 bits
      [2] bit0-1 steering high 2 bits, bits 2-7 padding
      [3] Y axis, 6 bits
      [4] Rz axis, 6 bits
      [5] buttons / vendor bits
      [6] padding

    Steering is a 10-bit signed field sitting in the low bits of a 16-bit
    little-endian word: mask to 10 bits, then sign-extend from bit 9.
    """
    if len(data) < REPORT_SIZE or data[0] != REPORT_ID_STEERING:
        return None
    x = (data[1] | (data[2] << 8)) & 0x3FF
    if x >= 512:
        x -= 1024
    return x, data[3] & 0x3F, data[4] & 0x3F, data[5]


def iter_steering(device_path, timeout=1.0):
    """Yield (x, y, rz, buttons) for every steering report."""
    fd, _ = open_hidraw(device_path)
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                continue
            try:
                data = os.read(fd, 64)
            except BlockingIOError:
                continue
            decoded = decode_steering_report(data)
            if decoded is not None:
                yield decoded
    finally:
        os.close(fd)


def cmd_read(args):
    for x, _, _, _ in iter_steering(args.device):
        print(x if args.raw else "%+0.3f" % norm(x), flush=True)


def cmd_watch(args):
    interval = 1.0 / args.hz if args.hz else 0
    last = 0.0
    for x, y, rz, buttons in iter_steering(args.device):
        now = time.monotonic()
        if interval and now - last < interval:
            continue
        last = now
        print("steer %+5d  norm %+0.3f  y %2d  rz %2d  buttons 0x%02x"
              % (x, norm(x), y, rz, buttons), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("read", help="stream steering position")
    p.add_argument("--raw", action="store_true", help="print raw values, not normalized")
    p.add_argument("--device", help="hidraw node (default: autodetect)")
    p.set_defaults(func=cmd_read)

    p = subparsers.add_parser("watch", help="stream steering plus pedals and buttons")
    p.add_argument("--hz", type=float, default=0, help="rate limit in Hz (0 = every report)")
    p.add_argument("--device", help="hidraw node (default: autodetect)")
    p.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()