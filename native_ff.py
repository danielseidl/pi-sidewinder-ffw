#!/usr/bin/env python3
"""Drive the Microsoft SideWinder Force Feedback Wheel.

This is the 1998 USB wheel:
https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel

The protocol work lives in wheelctl.py, which this and mcp_server.py share.
In short: the kernel's hid-pidff mis-encodes force feedback for this device, so
we write the PID output reports over hidraw ourselves, and we must use effect
block 1 rather than the block the device's feature report advertises. See
wheelctl.py for the full account.

Modes
-----
  center [seconds] [--strength N]   spring holding the wheel at centre (default)
  damper [seconds] [--strength N]   resistance proportional to turning speed
  constant LEVEL [seconds]          constant force, LEVEL in -255..255
  sweep                             constant force swept right -> left
  off                               stop all effects, disable the actuators

`center` and `damper` stay engaged until stopped, unless a duration is given.
Add -v to print the reports being sent.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wheelctl


def open_or_exit(device):
    try:
        return wheelctl.open_wheel(device)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    except PermissionError:
        sys.exit("permission denied on the wheel's hidraw node -- run as root, "
                 "or install the udev rule described in README.md")


def describe(fd, verbose):
    if verbose:
        driver_block = wheelctl.probe_blocks(fd)
        print("using effect block %d" % wheelctl.BLOCK, file=sys.stderr)
        if driver_block is not None and driver_block != wheelctl.BLOCK:
            print("  (the driver allocated block %d; effects there produce no "
                  "force)" % driver_block, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", nargs="?", default="center",
                        choices=["center", "damper", "constant", "sweep", "off"])
    parser.add_argument("args", nargs="*", type=float,
                        help="seconds (center/damper), or LEVEL and seconds (constant)")
    parser.add_argument("--strength", type=int, default=63,
                        help="spring/damper coefficient, 0-127 (default 63)")
    parser.add_argument("--device", help="hidraw node (default: autodetect)")
    parser.add_argument("-v", "--verbose", action="store_true")
    options = parser.parse_args()

    fd = open_or_exit(options.device)
    try:
        describe(fd, options.verbose)

        if options.mode == "off":
            wheelctl.stop_all(fd)
            print("all effects stopped, actuators disabled", file=sys.stderr)
            return

        if options.mode == "constant":
            if not options.args:
                sys.exit("constant needs a level, e.g. 'constant -150 2'")
            level = int(options.args[0])
            seconds = options.args[1] if len(options.args) > 1 else 3.0
            print("constant force %+d for %.1fs" % (level, seconds), file=sys.stderr)
            wheelctl.run_constant(fd, level, seconds)
            return

        if options.mode == "sweep":
            print("sweeping constant force right -> left", file=sys.stderr)
            wheelctl.run_sweep(fd, 0)
            print("done", file=sys.stderr)
            return

        seconds = options.args[0] if options.args else None
        effect = wheelctl.ET_SPRING if options.mode == "center" else wheelctl.ET_DAMPER
        label = "centre spring" if options.mode == "center" else "damper"
        suffix = " for %.0fs" % seconds if seconds else " until stopped"
        print("%s: %s%s" % (options.mode, label, suffix), file=sys.stderr)
        wheelctl.run_condition(fd, effect, seconds, coeff=options.strength)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()