#!/usr/bin/env python3
"""Microsoft SideWinder Force Feedback Wheel: read steering, drive force feedback.

Tested on a Raspberry Pi (Debian 13, kernel 6.18) with the USB model
(045e:0034). The wheel is a force-feedback HID device: the kernel binds
hid-generic + hid-pidff and exposes an input node, but on the unit tested the
input node never delivered reports. Raw HID reports do arrive, so this tool
reads the wheel straight from hidraw and drives force feedback through evdev.

  read [--raw] [--device PATH]        stream steering position
  watch [--hz N] [--device PATH]      same, but one line per report with buttons
  ff play --level L [--ms N] [--gain G]
  ff demo                             interactive force-feedback playground

Reading requires permission on the hidraw node; force feedback requires
permission on the event node. Run as root, or install the udev rule in
README.md.
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
MAX_LEVEL = 0x7FFF

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


def load_evdev():
    try:
        import evdev
        from evdev import ecodes, ff
    except ImportError:
        sys.exit("force feedback needs the 'evdev' package:\n"
                 "  python3 -m venv ~/.venv && ~/.venv/bin/pip install evdev")
    return evdev, ecodes, ff


def find_ff_device(evdev, ecodes):
    """Find the input node that exposes both the steering axis and EV_FF."""
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        caps = device.capabilities()
        axes = [code for code, _ in caps.get(ecodes.EV_ABS, [])]
        if ecodes.ABS_X in axes and ecodes.EV_FF in caps:
            return device
    sys.exit("no force-feedback device found (need ABS_X + EV_FF)")


def condition_pair(ff, coeff, saturation=0xFFFF):
    """Spring/damper conditions are supplied as an array of two, one per axis.

    The evdev bindings expose the union member as a fixed-size ctypes array;
    build it explicitly and fill both slots rather than scaling a single
    Condition, which is not a supported operation.
    """
    pair = (ff.Condition * 2)()
    pair[0] = ff.Condition(saturation, saturation, coeff, coeff, 0, 0)
    pair[1] = ff.Condition(saturation, saturation, coeff, coeff, 0, 0)
    return pair


def new_effect(ff, ecodes, etype, replay_ms, **union):
    effect = ff.Effect()
    effect.type = etype
    effect.id = -1
    effect.direction = 0
    effect.ff_trigger = ff.Trigger(0, 0)
    effect.ff_replay = ff.Replay(replay_ms, 0)
    for name, value in union.items():
        if name == "ff_condition_effect":
            effect.u.ff_condition_effect = value
        else:
            setattr(effect.u, name, value)
    return effect


def set_gain(device, ecodes, percent):
    """FF_GAIN is written directly, not uploaded as an effect."""
    hundred = max(0, min(100, int(percent)))
    device.write(ecodes.EV_FF, ecodes.FF_GAIN, hundred * 0xFFFF // 100)


def upload_constant(device, ff, ecodes, level, ms):
    level = int(max(-1.0, min(1.0, level)) * MAX_LEVEL)
    effect = new_effect(
        ff, ecodes, ecodes.FF_CONSTANT, max(ms, 1),
        ff_constant_effect=ff.Constant(level, ff.Envelope(0, 0, 0, 0)))
    return device.upload_effect(effect)


def upload_spring(device, ff, ecodes, coeff=0x4000, saturation=0xFFFF):
    effect = new_effect(
        ff, ecodes, ecodes.FF_SPRING, 0xFFFF,
        ff_condition_effect=condition_pair(ff, coeff, saturation))
    return device.upload_effect(effect)


def upload_rumble(device, ff, ecodes, ms=400, magnitude=0x8000):
    effect = new_effect(
        ff, ecodes, ecodes.FF_RUMBLE, ms,
        ff_rumble_effect=ff.Rumble(magnitude, magnitude))
    return device.upload_effect(effect)


def cmd_ff_play(args):
    evdev, ecodes, ff = load_evdev()
    device = find_ff_device(evdev, ecodes)
    try:
        set_gain(device, ecodes, args.gain)
        effect_id = upload_constant(device, ff, ecodes, args.level, args.ms)
        try:
            device.write(ecodes.EV_FF, effect_id, 1)
            time.sleep(args.ms / 1000.0 + 0.05)
            device.write(ecodes.EV_FF, effect_id, 0)
        finally:
            device.erase_effect(effect_id)
        print("played level=%s for %sms at gain=%s%%"
              % (args.level, args.ms, args.gain))
    finally:
        device.close()


def cmd_ff_demo(args):
    evdev, ecodes, ff = load_evdev()
    device = find_ff_device(evdev, ecodes)
    try:
        set_gain(device, ecodes, 80)
        spring_id = upload_spring(device, ff, ecodes)
        constant_id = upload_constant(device, ff, ecodes, 0.6, 0xFFFF)
        rumble_id = upload_rumble(device, ff, ecodes)
        print("a = toggle spring   l/r = push left/right   f = rumble   "
              "c = stop   q = quit", file=sys.stderr)
        spring_on = False
        for line in sys.stdin:
            key = line.strip()[:1]
            if key in ("q", ""):
                break
            if key == "a":
                spring_on = not spring_on
                device.write(ecodes.EV_FF, spring_id, 1 if spring_on else 0)
                print("spring", "on" if spring_on else "off")
            elif key in "lr":
                device.erase_effect(constant_id)
                constant_id = upload_constant(
                    device, ff, ecodes, -0.6 if key == "l" else 0.6, 0xFFFF)
                device.write(ecodes.EV_FF, constant_id, 1)
                print("push", "left" if key == "l" else "right")
            elif key == "f":
                device.erase_effect(rumble_id)
                rumble_id = upload_rumble(device, ff, ecodes, 500)
                device.write(ecodes.EV_FF, rumble_id, 1)
                print("rumble")
            elif key == "c":
                for effect_id in (spring_id, constant_id, rumble_id):
                    device.write(ecodes.EV_FF, effect_id, 0)
                print("stopped")
        for effect_id in (spring_id, constant_id, rumble_id):
            device.erase_effect(effect_id)
    finally:
        device.close()


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

    ff_parser = subparsers.add_parser("ff", help="force feedback")
    ff_sub = ff_parser.add_subparsers(dest="ff_command", required=True)

    p = ff_sub.add_parser("play", help="play a constant-force effect")
    p.add_argument("--level", type=float, default=0.5, help="-1.0 .. +1.0")
    p.add_argument("--ms", type=int, default=500, help="duration in milliseconds")
    p.add_argument("--gain", type=int, default=80, help="master gain, 0-100")
    p.set_defaults(func=cmd_ff_play)

    p = ff_sub.add_parser("demo", help="interactive force-feedback playground")
    p.set_defaults(func=cmd_ff_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()