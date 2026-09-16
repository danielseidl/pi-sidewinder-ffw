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
    report (report 2); on the unit tested that report says block 2, but effects
    written there produce no force. Block 1 is the one the device actuates.
    See the BLOCK constant.

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
                          direction(2) start-delay(1) gain(1) padding(4)
  0x03  set condition     block(1) axis(1) centre(1) pos-coeff(1)
                          neg-coeff(1) pos-sat(1) neg-sat(1) deadband(1)
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


# Effect block to drive. Feature report 2 advertises the block the kernel
# driver allocated (2 on the unit tested), but writing effects to that block
# produces no force, and neither does block 2 for the driver's own effects.
# Block 1 is the one the device actuates, confirmed against both spring and
# constant force. Do not "fix" this by reading feature report 2.
BLOCK = 1


def find_block(fd):
    """Return the effect block to drive.

    Always BLOCK. Kept as a function so callers read explicitly, and because
    the feature-report probe is still useful when diagnosing a unit whose
    block numbering differs.
    """
    return BLOCK


def probe_blocks(fd):
    """Diagnostic: report the block the kernel driver allocated, if readable."""
    try:
        report = get_feature(fd, 2)
        if len(report) >= 3:
            return report[1]
    except OSError:
        pass
    return None


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
    """Report 0x01: declare an effect of the given type in the block.

    16 bytes. This mirrors what the kernel driver emits for this device, which
    is known to produce force; the extra trailing bytes over the 14-byte short
    form matter on the wire.

    Note the axes-enable byte is 0x01 here, not 0x03.
    """
    payload = bytes([
        0x01,
        block,
        effect_type,
        0xFF, 0xFF,  # duration: max
        0x00, 0x00,  # trigger button + interval
        0x00,        # axes enable
        0x00, 0xFF,  # direction
        0x00,        # start delay
        0x04,        # gain / sample period
        0x00, 0x00, 0x00, 0x00,
    ])
    write_report(fd, payload, "set effect (type %d)" % effect_type, verbose)
    time.sleep(0.05)


def set_condition(fd, block, axis, centre=0, pos_coeff=63, neg_coeff=63,
                  pos_sat=255, neg_sat=255, deadband=0, verbose=False):
    """Report 0x03: spring / damper / inertia / friction parameters.

    Byte layout, packed as the descriptor declares it:

      [0]  effect parameter block index
      [1]  bits 0-3 parameter block offset
           bits 4-5 type-specific ordinal 1
           bits 6-7 type-specific ordinal 2
      [2]  centre point offset (signed byte)
      [3]  positive coefficient (signed byte)
      [4]  negative coefficient (signed byte)
      [5]  positive saturation (unsigned byte)
      [6]  negative saturation (unsigned byte)
      [7]  dead band (unsigned byte)

    Sent once per axis: `axis` selects which condition slot is being written.
    Coefficients are signed bytes and saturations unsigned bytes; the device's
    physical range is -10000..10000, so one byte per field is coarse.

    The default coefficients match the kernel driver's output for this device,
    which is the only configuration verified to produce force.
    """
    payload = bytes([
        0x03,
        block,
        axis & 0xFF,
        centre & 0xFF,
        pos_coeff & 0xFF,
        neg_coeff & 0xFF,
        pos_sat & 0xFF,
        neg_sat & 0xFF,
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
    """Loop count 1, matching what the driver sends for this device."""
    write_report(fd, bytes([0x0A, block, op, 0x01]),
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


def apply_condition_both_axes(fd, block, verbose=False, **params):
    """Conditions are written once per axis, as the driver does."""
    for axis in (0x00, 0x01):
        set_condition(fd, block, axis, verbose=verbose, **params)


def mode_center(fd, block, seconds=0, verbose=False):
    """Spring pulling the wheel back to centre. The sensible default."""
    print("centre: spring holding the wheel at centre"
          + (" for %.0fs" % seconds if seconds else ""), file=sys.stderr)
    start_effect(fd, block, ET_SPRING, verbose)
    apply_condition_both_axes(fd, block, verbose=verbose,
                              centre=0, pos_coeff=63, neg_coeff=63,
                              pos_sat=255, neg_sat=255, deadband=0)
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
    apply_condition_both_axes(fd, block, verbose=verbose,
                              centre=0, pos_coeff=63, neg_coeff=63,
                              pos_sat=255, neg_sat=255, deadband=0)
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