#!/usr/bin/env python3
"""Read the Microsoft SideWinder Force Feedback Wheel's steering position.

This is the 1998 USB wheel:
https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel

The kernel binds hid-generic + hid-pidff and exposes an input node, but on the
unit tested the input node never delivered reports. Raw HID reports do arrive,
so this reads the wheel straight from hidraw. Force feedback is handled by
native_ff.py; both share wheelctl.py.

  read [--raw] [--device PATH]     stream steering position
  watch [--hz N] [--device PATH]   steering plus the aux axes and buttons

Reading requires permission on the hidraw node. Run as root, or install the
udev rule in README.md.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wheelctl


def open_or_exit(device):
    try:
        return wheelctl.open_wheel(device), None
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    except PermissionError:
        sys.exit("permission denied on the wheel's hidraw node -- run as root, "
                 "or install the udev rule described in README.md")


def cmd_read(args):
    fd, path = open_or_exit(args.device)
    print("# reading %s" % (path or wheelctl.find_hidraw()), file=sys.stderr)
    try:
        while True:
            sample = wheelctl.read_sample(fd, timeout=5.0)
            if sample is None:
                continue
            print(sample["raw"] if args.raw else "%+0.3f" % sample["normalized"],
                  flush=True)
    finally:
        os.close(fd)


def cmd_watch(args):
    fd, path = open_or_exit(args.device)
    print("# reading %s" % (path or wheelctl.find_hidraw()), file=sys.stderr)
    interval = 1.0 / args.hz if args.hz else 0
    last = 0.0
    try:
        while True:
            sample = wheelctl.read_sample(fd, timeout=5.0)
            if sample is None:
                continue
            now = __import__("time").monotonic()
            if interval and now - last < interval:
                continue
            last = now
            print("steer %+5d  norm %+0.3f  y %2d  rz %2d  buttons 0x%02x"
                  % (sample["raw"], sample["normalized"], sample["y"],
                     sample["rz"], sample["buttons"]), flush=True)
    finally:
        os.close(fd)


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