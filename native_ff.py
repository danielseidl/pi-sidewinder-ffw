#!/usr/bin/env python3
"""Drive the SideWinder Force Feedback Wheel's native PID protocol over hidraw.

Why this exists
---------------
The kernel's hid-pidff does bind this wheel, and evdev will happily upload
effects to it, but every effect comes out as a brief blip at imperceptible
force. Captured USB traffic shows why: the driver writes constant-force
magnitude into report 0x0c (PIDDeviceControl) instead of report 0x05
(SetConstantForceReport) and truncates the value to a single digit. This device
(1998) predates the HID PID spec and the driver mis-encodes for it.

Two things the standard path also never does, both required here:

  * PIDDeviceControl must be sent with EnableActuators (report 0x0c, value 1).
    Actuators are off by default, so effects are accepted and then ignored.
  * Effects must go to the parameter block the device actually created. The
    driver allocates one; its index is readable from the block-load feature
    report. Writing to an assumed index (e.g. 1) silently does nothing.

Report layouts, from /sys/kernel/debug/hid/<dev>/rdesc:

  0x01  set effect        block(1) type(1) duration(2) trigger(2) axes(1)
                          direction(2) start-delay(1) gain(1) sample-period(2)
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

EFFECT_CONSTANT = 0x0C
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


def write_report(fd, payload, label=""):
    written = os.write(fd, payload)
    if label:
        print("  -> %-26s %s" % (label, payload.hex()))
    return written


def device_control(fd, value):
    write_report(fd, bytes([0x0C, value]),
                 "device control (%s)" % {1: "enable actuators", 2: "disable", 3: "stop all"}.get(value, value))
    time.sleep(0.05)


def set_effect(fd, block):
    payload = bytes([
        0x01,
        block,
        EFFECT_CONSTANT,
        0xFF, 0x7F,  # duration: max
        0x00, 0x00,  # trigger button + interval
        0x03,        # axes enable: X and Y
        0x00, 0x00,  # direction
        0x00,        # start delay
        0xFF,        # gain, 100%
        0x00, 0x00,  # sample period, 0 = device default
    ])
    write_report(fd, payload, "set effect")


def set_constant(fd, block, level):
    level = max(-255, min(255, int(level)))
    write_report(fd, bytes([0x05, block]) + struct.pack("<h", level),
                 "set constant (%+d)" % level)
    time.sleep(0.05)


def effect_operation(fd, block, op):
    write_report(fd, bytes([0x0A, block, op, 0xFF]),
                 "effect op (%s)" % {OP_START: "start", OP_SOLO: "solo", OP_STOP: "stop"}.get(op, op))


def free_block(fd, block):
    write_report(fd, bytes([0x0B, block]), "block free")


def play(fd, block, level, seconds):
    device_control(fd, DC_ENABLE_ACTUATORS)
    set_effect(fd, block)
    set_constant(fd, block, level)
    effect_operation(fd, block, OP_START)
    print("  playing %+d for %.1fs" % (level, seconds))
    time.sleep(seconds)
    effect_operation(fd, block, OP_STOP)
    free_block(fd, block)


def sweep(fd, block):
    print("sweeping constant force right -> left")
    device_control(fd, DC_ENABLE_ACTUATORS)
    for level in (255, 200, 150, 100, 60, 30, 0, -30, -60, -100, -150, -200, -255):
        set_effect(fd, block)
        set_constant(fd, block, level)
        effect_operation(fd, block, OP_START)
        print("  level %+4d" % level, flush=True)
        time.sleep(1.2)
        effect_operation(fd, block, OP_STOP)
        free_block(fd, block)
        time.sleep(0.2)
    print("done")


def main():
    fd = open_wheel()
    try:
        block = find_block(fd)
        print("using effect block %d" % block, file=sys.stderr)
        if len(sys.argv) > 1 and sys.argv[1] == "sweep":
            sweep(fd, block)
        else:
            level = int(sys.argv[1]) if len(sys.argv) > 1 else 200
            seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
            play(fd, block, level, seconds)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()