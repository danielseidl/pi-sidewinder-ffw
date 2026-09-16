#!/usr/bin/env python3
"""Drive the Microsoft SideWinder Force Feedback Wheel's native PID protocol.

This is the 1998 USB wheel: https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel

Why this exists
---------------
The kernel's hid-pidff does bind this wheel, and evdev will happily upload
effects to it and report success, but every effect comes out as a brief blip at
imperceptible force. Captured USB traffic shows why: the driver writes
constant-force magnitude into report 0x0c (PIDDeviceControl) instead of report
0x05 (SetConstantForceReport), truncated to a single digit. This device predates
the HID PID specification, and the driver mis-encodes for it.

Two further steps are required that the standard path never performs:

  * PIDDeviceControl must be sent with EnableActuators (report 0x0c, value 1).
    Actuators are off by default, so effects are accepted and then ignored.
  * Effects must go to the parameter block the device actually allocated. The
    driver allocates one, and its index is readable from the block-load feature
    report (report 2); on the unit tested it is block 2, not the 1 you would
    assume. Writing to a non-existent block silently does nothing.

This tool writes the PID output reports directly over hidraw, which is why it
needs no evdev and works despite the driver.

Modes
-----
  center       spring holding the wheel at centre (default)
  damper       resistance proportional to turning speed
  constant L   constant force, L in -255..255 (negative = left)
  sweep        constant force swept from full-right to full-left, for testing
  off          stop all effects and disable the actuators

Report layouts, from /sys/kernel/debug/hid/<dev>/rdesc:

  0x01  set effect        block(1) type(1) duration(2) trigger(2) axes(1)
                          direction(2) start-delay(1) gain(1) sample-period(2)
  0x03  set condition     block(1) ...(1) axis-enable(1) centre(1)
                          pos-coeff(1) neg-coeff(1) pos-sat(2) neg-sat(2)
                          deadband(1)
  0x05  set constant      block(1) level(int16, -255..255)
  0x0a  effect operation  block(1) operation(1) loop-count(1)
  0x0b  block free        block(1)
  0x0c  device control    value(1): 1=enable 2=disable 3=stop-all 4=reset
"""

import ctypes
import fcntl
import os
import struct
import sys
import time

DEV = "/dev/hidraw0"

ET_CONSTANT = 1
ET_SPRING = 8
ET_DAMPER = 9

OP_START, OP_SOLO, OP_STOP = 1, 2, 3
DC_ENABLE_ACTUATORS = 1
DC_DISABLE_ACTUATORS = 2
DC_STOP_ALL = 3

_IOC_RW = (2 | 1) << 30


def _gf_number(size):
    return _IOC_RW | ((size & 0x3FFF) << 16) | (ord("H") << 8) | 0x07


def get_feature(fd, report_id, size=64):
    buf = ctypes.create_string_buffer(size + 1)
    buf[0] = bytes([report_id])
    length = fcntl.ioctl(fd, _gf_number(size + 1), buf)
    return bytes(buf.raw[:length])


def open_wheel(path=DEV):
    try:
        return os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        sys.exit("permission denied on %s -- run as root, or install the udev "
                 "rule described in README.md" % path)
    except FileNotFoundError:
        sys.exit("no such device: %s" % path)


def find_block(fd):
    """Read the effect block index the device has allocated.

    Feature report 2 is the block-load report: report id, block index, status.
    Status 1 means loaded. Fall back to block 1 if the report is unavailable.
    """
    try:
        report = get_feature(fd, 2)
        if len(report) >= 3:
            return report[1]
    except OSError:
        pass
    return 1


def write_report(fd, payload, label="", verbose=False):
    written = os.write(fd, payload)
    if verbose and label:
        print("  -> %-24s %s" % (label, payload.hex()))
    return written


def device_control(fd, value, verbose=False):
    names = {1: "enable actuators", 2: "disable", 3: "stop all", 4: "reset"}
    write_report(fd, bytes([0x0C, value]),
                 "device control (%s)" % names.get(value, value), verbose)
    time.sleep(0.05)


def declare_effect(fd, block, effect_type, verbose=False):
    """Report 0x01: declare an effect of the given type in the block."""
    payload = bytes([
        0x01,
        block,
        effect_type,
        0xFF, 0x7F,  # duration: max
        0x00, 0x00,  # trigger button + interval
        0x03,        # axes enable: X and Y
        0x00, 0x00,  # direction
        0x00,        # start delay
        0xFF,        # gain, 100%
        0x00, 0x00,  # sample period, 0 = device default
    ])
    write_report(fd, payload, "set effect (type %d)" % effect_type, verbose)
    time.sleep(0.05)


def set_condition(fd, block, centre=0, pos_coeff=127, neg_coeff=-127,
                  pos_sat=255, neg_sat=255, deadband=0, verbose=False):
    """Report 0x03: spring / damper / inertia / friction parameters.

    Coefficients and centre are signed bytes. Saturation and deadband are
    unsigned. All values use the device's own reduced range, not the PID spec's
    0..10000.
    """
    payload = bytes([
        0x03,
        block,
        deadband & 0xFF,
        0x03,                              # axis enable: X and Y
        centre & 0xFF,
        pos_coeff & 0xFF,
        neg_coeff & 0xFF,
        pos_sat & 0xFF, (pos_sat >> 8) & 0xFF,
        neg_sat & 0xFF, (neg_sat >> 8) & 0xFF,
        deadband & 0xFF,
    ])
    write_report(fd, payload, "set condition", verbose)
    time.sleep(0.05)


def set_constant(fd, block, level, verbose=False):
    """Report 0x05: constant-force magnitude. Device range is -255..255."""
    level = max(-255, min(255, int(level)))
    write_report(fd, bytes([0x05, block]) + struct.pack("<h", level),
                 "set constant (%+d)" % level, verbose)
    time.sleep(0.05)


def effect_operation(fd, block, op, verbose=False):
    write_report(fd, bytes([0x0A, block, op, 0xFF]),
                 "effect op (%s)" % {1: "start", 2: "solo", 3: "stop"}.get(op, op),
                 verbose)


def free_block(fd, block, verbose=False):
    write_report(fd, bytes([0x0B, block]), "block free", verbose)


def stop_all(fd, verbose=False):
    device_control(fd, DC_STOP_ALL, verbose)
    device_control(fd, DC_DISABLE_ACTUATORS, verbose)


def start_effect(fd, block, effect_type, verbose=False):
    """Enable the actuators, declare the effect type, and start it.

    Returns after the effect is running; the caller keeps the block alive.
    """
    device_control(fd, DC_ENABLE_ACTUATORS, verbose)
    declare_effect(fd, block, effect_type, verbose)


def mode_center(fd, block, seconds=0, verbose=False):
    """Spring pulling the wheel back to centre. The sensible default."""
    print("centre: spring holding the wheel at centre"
          + (" for %.0fs" % seconds if seconds else ""), file=sys.stderr)
    start_effect(fd, block, ET_SPRING, verbose)
    set_condition(fd, block, centre=0, pos_coeff=127, neg_coeff=-127,
                  pos_sat=255, neg_sat=255, deadband=0, verbose=verbose)
    effect_operation(fd, block, OP_START, verbose)
    if seconds:
        time.sleep(seconds)
        effect_operation(fd, block, OP_STOP, verbose)
        free_block(fd, block, verbose)


def mode_damper(fd, block, seconds=0, verbose=False):
    """Resistance proportional to how fast the wheel is turned."""
    print("damper: resistance proportional to turning speed"
          + (" for %.0fs" % seconds if seconds else ""), file=sys.stderr)
    start_effect(fd, block, ET_DAMPER, verbose)
    set_condition(fd, block, centre=0, pos_coeff=127, neg_coeff=-127,
                  pos_sat=255, neg_sat=255, deadband=0, verbose=verbose)
    effect_operation(fd, block, OP_START, verbose)
    if seconds:
        time.sleep(seconds)
        effect_operation(fd, block, OP_STOP, verbose)
        free_block(fd, block, verbose)


def mode_constant(fd, block, level, seconds, verbose=False):
    print("constant force %+d for %.1fs" % (level, seconds), file=sys.stderr)
    start_effect(fd, block, ET_CONSTANT, verbose)
    set_constant(fd, block, level, verbose)
    effect_operation(fd, block, OP_START, verbose)
    time.sleep(seconds)
    effect_operation(fd, block, OP_STOP, verbose)
    free_block(fd, block, verbose)


def mode_sweep(fd, block, verbose=False):
    print("sweeping constant force right -> left", file=sys.stderr)
    device_control(fd, DC_ENABLE_ACTUATORS, verbose)
    for level in (255, 200, 150, 100, 60, 30, 0, -30, -60, -100, -150, -200, -255):
        declare_effect(fd, block, ET_CONSTANT, verbose)
        set_constant(fd, block, level, verbose)
        effect_operation(fd, block, OP_START, verbose)
        print("  level %+4d" % level, flush=True)
        time.sleep(1.2)
        effect_operation(fd, block, OP_STOP, verbose)
        free_block(fd, block, verbose)
        time.sleep(0.2)
    print("done", file=sys.stderr)


def parse(argv):
    if not argv:
        return "center", [], False
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    mode = argv[0]
    return mode, argv[1:], verbose


def main():
    mode, rest, verbose = parse(sys.argv[1:])

    if mode == "off":
        fd = open_wheel()
        try:
            stop_all(fd, verbose)
            print("all effects stopped, actuators disabled", file=sys.stderr)
        finally:
            os.close(fd)
        return

    fd = open_wheel()
    try:
        block = find_block(fd)
        if verbose:
            print("using effect block %d" % block, file=sys.stderr)

        if mode == "center":
            mode_center(fd, block, seconds=float(rest[0]) if rest else 0, verbose=verbose)
        elif mode == "damper":
            mode_damper(fd, block, seconds=float(rest[0]) if rest else 0, verbose=verbose)
        elif mode == "constant":
            if not rest:
                sys.exit("constant needs a level, e.g. 'constant -150 2.0'")
            level = int(rest[0])
            seconds = float(rest[1]) if len(rest) > 1 else 3.0
            mode_constant(fd, block, level, seconds, verbose)
        elif mode == "sweep":
            mode_sweep(fd, block, verbose)
        else:
            sys.exit("unknown mode %r\n"
                     "modes: center, damper, constant, sweep, off" % mode)
    finally:
        if mode == "center" or mode == "damper":
            pass  # spring/damper stay engaged until `off` or the process exits
        os.close(fd)


if __name__ == "__main__":
    main()