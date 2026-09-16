#!/usr/bin/env python3
"""Shared control and reading logic for the Microsoft SideWinder Force
Feedback Wheel.

This is the 1998 USB wheel:
https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel

Both native_ff.py (command line) and mcp_server.py import this, so the protocol
knowledge lives in one place.

Reading
-------
The wheel sends HID report ID 1, seven bytes:

    [0] report ID (0x01)
    [1] steering low 8 bits
    [2] bits 0-1 steering high 2 bits, bits 2-7 padding
    [3] Y axis, 6 bits
    [4] Rz axis, 6 bits
    [5] buttons / vendor bits
    [6] padding

Steering is a 10-bit signed field. The two high bits are packed into the low
bits of byte 2, so the value must be masked to 10 bits and then sign-extended
from bit 9 rather than read as a little-endian 16-bit integer: a plain 16-bit
read turns every leftward position into a large positive number.

Force feedback
--------------
The kernel's hid-pidff does bind this wheel, and evdev will happily upload
effects to it and report success, but every effect comes out as a brief blip at
imperceptible force. Captured USB traffic shows why: the driver writes
constant-force magnitude into report 0x0c (PIDDeviceControl) instead of report
0x05 (SetConstantForceReport), truncated to a single digit. This device predates
the HID PID specification and the driver mis-encodes for it.

So we write the PID output reports directly over hidraw. Two steps are required
that the standard path never performs:

  * PIDDeviceControl must be sent with EnableActuators (report 0x0c, value 1).
    Actuators are off by default, so effects are accepted and then ignored.
  * Effects must use block 1. Feature report 2 advertises the block the driver
    allocated (2 on the unit tested), but effects written there produce no
    force. See BLOCK.

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
import select
import struct
import time

VENDOR_ID = "045e"
PRODUCT_ID = "0034"

DEFAULT_DEVICE = "/dev/hidraw0"

AXIS_MIN = -512
AXIS_MAX = 511

REPORT_ID_STEERING = 0x01
REPORT_SIZE = 7

ET_CONSTANT = 1
ET_SPRING = 8
ET_DAMPER = 9

OP_START, OP_SOLO, OP_STOP = 1, 2, 3
DC_ENABLE_ACTUATORS = 1
DC_DISABLE_ACTUATORS = 2
DC_STOP_ALL = 3
DC_RESET = 4

# Effect block to drive. Feature report 2 advertises the block the kernel
# driver allocated (2 on the unit tested), but writing effects to that block
# produces no force, and neither does it for the driver's own effects. Block 1
# is the one the device actuates, confirmed against both spring and constant
# force. Do not "fix" this by reading feature report 2.
BLOCK = 1

_IOC_RW = (2 | 1) << 30


# --------------------------------------------------------------------------
# device discovery
# --------------------------------------------------------------------------

def find_hidraw():
    """Locate the wheel's hidraw node by USB ID (hidraw numbers are not stable).

    Returns the node path, or None if the wheel is not connected.
    """
    for node, _ in _iter_hidraw():
        return node
    return None


def _iter_hidraw():
    """Yield (node_path, uevent_dict) for every matching hidraw device."""
    root = "/sys/class/hidraw"
    if not os.path.isdir(root):
        return
    for entry in sorted(os.listdir(root)):
        uevent_path = os.path.join(root, entry, "device", "uevent")
        try:
            with open(uevent_path) as handle:
                uevent = handle.read()
        except OSError:
            continue
        fields = {}
        for line in uevent.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        hid_id = fields.get("HID_ID", "")
        parts = hid_id.split(":")
        if len(parts) == 3 and parts[1].endswith(VENDOR_ID.upper()) \
                and parts[2].endswith(PRODUCT_ID.upper()):
            yield "/dev/" + entry, fields


def device_name(path=None):
    """Read the wheel's HID name from sysfs uevent (hidraw has no name ioctl)."""
    for node, fields in _iter_hidraw():
        if path is None or node == path:
            return fields.get("HID_NAME")
    return None


def open_wheel(path=None):
    """Open the wheel read/write over hidraw."""
    if path is None:
        path = find_hidraw()
    if path is None:
        raise FileNotFoundError(
            "could not find the wheel's hidraw node; is it plugged in?")
    return os.open(path, os.O_RDWR | os.O_NONBLOCK)


def probe(path=None):
    """Return (present, detail) without raising, for status reporting."""
    if path is None:
        path = find_hidraw()
    detail = {"path": path}
    if path is None:
        detail["error"] = "wheel not found on any hidraw node"
        return False, detail
    if not os.path.exists(path):
        detail["error"] = "no such device: %s" % path
        return False, detail
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError as exc:
        detail["error"] = "permission denied: %s" % exc
        return False, detail
    except OSError as exc:
        detail["error"] = str(exc)
        return False, detail
    os.close(fd)
    detail["name"] = device_name(path)
    return True, detail


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def decode_steering_report(data):
    """Decode report ID 1 into (raw, y, rz, buttons), or None if not that report."""
    if len(data) < REPORT_SIZE or data[0] != REPORT_ID_STEERING:
        return None
    raw = (data[1] | (data[2] << 8)) & 0x3FF
    if raw >= 512:
        raw -= 1024
    sample = {
        "raw": raw,
        "normalized": normalize(raw),
        "y": data[3] & 0x3F,
        "rz": data[4] & 0x3F,
        "buttons": data[5],
        "buttons_set": button_names(data[5]),
        "pedals": {
            "y": pedal_fraction(data[3] & 0x3F),
            "rz": pedal_fraction(data[4] & 0x3F),
        },
    }
    return sample


# The wheel reports its buttons as one bit per button in the button byte.
# Six bits were observed to change on the unit tested (0x01..0x20); the upper
# two are documented by the descriptor as eight buttons total.
BUTTON_BITS = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)

PEDAL_REST = 0x3F


def button_names(byte):
    """Return the names of the buttons currently pressed, e.g. ['button1']."""
    return ["button%d" % (index + 1) for index, bit in enumerate(BUTTON_BITS)
            if byte & bit]


def pedal_fraction(value):
    """Map a pedal axis (0..63) to 0.0 (rest) .. 1.0 (fully pressed).

    The pedals rest at 0x3f, so a higher reading means less pressure. Callers
    wanting "how hard is it pressed" want this; the raw axis is also returned.
    """
    value = max(0, min(PEDAL_REST, value))
    return round((PEDAL_REST - value) / PEDAL_REST, 3)


def normalize(raw):
    """Map a raw steering value to -1.0 (full left) .. +1.0 (full right)."""
    return round(2.0 * (raw - AXIS_MIN) / (AXIS_MAX - AXIS_MIN) - 1.0, 3)


def read_sample(fd, timeout=1.0):
    """Wait for one steering report. Returns a sample dict, or None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return None
        try:
            data = os.read(fd, 64)
        except BlockingIOError:
            continue
        sample = decode_steering_report(data)
        if sample is not None:
            return sample


def collect(fd, seconds=3.0, max_samples=200):
    """Sample steering reports for a window. Returns a list of samples."""
    samples = []
    deadline = time.monotonic() + seconds
    while len(samples) < max_samples:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sample = read_sample(fd, timeout=remaining)
        if sample is None:
            break
        samples.append(sample)
    return samples


# --------------------------------------------------------------------------
# force feedback
# --------------------------------------------------------------------------

def _get_feature(fd, report_id, size=64):
    buf = ctypes.create_string_buffer(size + 1)
    buf[0] = bytes([report_id])
    length = fcntl.ioctl(fd, _IOC_RW | ((size & 0x3FFF) << 16) | (ord("H") << 8) | 0x07, buf)
    return bytes(buf.raw[:length])


def probe_blocks(fd):
    """Diagnostic: the block index the kernel driver allocated, if readable."""
    try:
        report = _get_feature(fd, 2)
        if len(report) >= 3:
            return report[1]
    except OSError:
        pass
    return None


def _write(fd, payload):
    return os.write(fd, payload)


def device_control(fd, value):
    """Report 0x0c: PID device control. Actuators are off until enabled."""
    _write(fd, bytes([0x0C, value]))
    time.sleep(0.05)


def stop_all(fd):
    """Stop every effect and disable the actuators, releasing the wheel."""
    device_control(fd, DC_STOP_ALL)
    device_control(fd, DC_DISABLE_ACTUATORS)


def declare_effect(fd, effect_type, block=BLOCK):
    """Report 0x01: declare an effect of the given type in the block.

    Mirrors what the kernel driver emits for this device, which is known to
    produce force. Note the axes-enable byte is 0x00, not the 0x03 that the
    descriptor's axis count might suggest.
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
    _write(fd, payload)
    time.sleep(0.05)


def set_condition(fd, axis, coeff=63, centre=0, saturation=255, deadband=0,
                  block=BLOCK):
    """Report 0x03: spring / damper / inertia / friction parameters.

    Sent once per axis; the device expects a report for each.

    Byte layout, as the descriptor declares it:

      [0]  effect parameter block index
      [1]  axis selector
      [2]  centre point offset (signed byte)
      [3]  positive coefficient (signed byte)
      [4]  negative coefficient (signed byte)
      [5]  positive saturation (unsigned byte)
      [6]  negative saturation (unsigned byte)
      [7]  dead band (unsigned byte)

    Coefficients default to +63 for both directions, which is what the kernel
    driver sends and the only configuration verified to produce force.
    """
    payload = bytes([
        0x03,
        block,
        axis & 0xFF,
        centre & 0xFF,
        coeff & 0xFF,
        coeff & 0xFF,
        saturation & 0xFF,
        saturation & 0xFF,
        deadband & 0xFF,
    ])
    _write(fd, payload)
    time.sleep(0.05)


def set_constant(fd, level, block=BLOCK):
    """Report 0x05: constant-force magnitude. Device range is -255..255."""
    level = max(-255, min(255, int(level)))
    _write(fd, bytes([0x05, block]) + struct.pack("<h", level))
    time.sleep(0.05)


def effect_operation(fd, operation, block=BLOCK):
    """Report 0x0a: start or stop the effect. Loop count 1, as the driver sends."""
    _write(fd, bytes([0x0A, block, operation, 0x01]))


def free_block(fd, block=BLOCK):
    """Report 0x0b: release the effect block."""
    _write(fd, bytes([0x0B, block]))


def wait(fd, seconds, stop_flag=None):
    """Sleep for `seconds`, or until stop_flag is set. Returns True if stopped."""
    deadline = time.monotonic() + seconds
    while True:
        if stop_flag is not None and stop_flag.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def start_effect(fd, effect_type, block=BLOCK):
    """Enable the actuators and declare the effect, leaving it ready to start."""
    device_control(fd, DC_ENABLE_ACTUATORS)
    declare_effect(fd, effect_type, block)


def run_constant(fd, level, seconds, stop_flag=None, block=BLOCK):
    """Play a constant force for `seconds`, or until stop_flag is set."""
    start_effect(fd, ET_CONSTANT, block)
    set_constant(fd, level, block)
    effect_operation(fd, OP_START, block)
    stopped = wait(fd, seconds, stop_flag)
    effect_operation(fd, OP_STOP, block)
    free_block(fd, block)
    return stopped


def run_condition(fd, effect_type, seconds, stop_flag=None, coeff=63, block=BLOCK):
    """Engage a spring or damper.

    With seconds=None the effect stays engaged until stop_flag is set, which is
    what the "leave it centring" modes want.
    """
    start_effect(fd, effect_type, block)
    for axis in (0x00, 0x01):
        set_condition(fd, axis, coeff=coeff, block=block)
    effect_operation(fd, OP_START, block)
    if seconds is None:
        if stop_flag is not None:
            while not stop_flag.is_set():
                time.sleep(0.05)
        else:
            return False
    else:
        wait(fd, seconds, stop_flag)
    effect_operation(fd, OP_STOP, block)
    free_block(fd, block)
    return True


SWEEP_LEVELS = (255, 200, 150, 100, 60, 30, 0, -30, -60, -100, -150, -200, -255)


def run_sweep(fd, seconds, stop_flag=None, step_seconds=1.2, block=BLOCK):
    """Sweep constant force from full-right to full-left."""
    device_control(fd, DC_ENABLE_ACTUATORS)
    for level in SWEEP_LEVELS:
        if stop_flag is not None and stop_flag.is_set():
            break
        declare_effect(fd, ET_CONSTANT, block)
        set_constant(fd, level, block)
        effect_operation(fd, OP_START, block)
        wait(fd, step_seconds, stop_flag)
        effect_operation(fd, OP_STOP, block)
        free_block(fd, block)
        time.sleep(0.2)