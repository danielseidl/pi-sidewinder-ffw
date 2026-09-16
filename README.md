# sidewinder-wheel

Read the steering position of a Microsoft SideWinder Force Feedback Wheel from a
Raspberry Pi (or any Linux box), and drive its force feedback.

Tested on a Raspberry Pi 5 running Debian 13 with kernel 6.18, against the USB
SideWinder Force Feedback Wheel (`045e:0034`).

## Power the wheel first

This wheel does not run off USB power. Its USB descriptor declares `bMaxPower`
of 100 mA, which cannot drive force-feedback motors, and the wheel's own HID
stack needs the external supply too: with the barrel-jack adapter unplugged, the
input node delivers *no reports at all* — no axis movement, no buttons — and the
FORCE LED flashes. Plug the adapter in and both the steering reports and force
feedback start working. If nothing here behaves, check that first.

## Why the standard force-feedback path does not work

The kernel binds `hid-generic` and its `hid-pidff` sub-driver, an input node
appears, and the device advertises the full HID PID feature set. `evdev` will
happily upload effects to it and report success. Every effect then comes out as
a brief blip at imperceptible force.

Captured USB traffic shows why. Asked for a constant-force effect at magnitude
29490, the driver writes this to the wire:

```
= 0c04      report 0x0c (PIDDeviceControl), value 4
```

`0x0c` is the device-control report, not the constant-force report, and the
magnitude has been truncated to a single digit. This wheel (1998) predates the
HID PID specification and the driver mis-encodes for it. Duration is mangled
the same way, which is what produces the blip.

Two further steps are required that the standard path never performs:

- **`PIDDeviceControl` must be sent with `EnableActuators`** (report `0x0c`,
  value `1`). Actuators are off by default, so effects are accepted and then
  ignored.
- **Effects must be written to the parameter block the device actually
  allocated.** The driver allocates one and its index is readable from the
  block-load feature report (report `2`); on the unit tested it is block `2`,
  not the `1` you would assume. Writing to a non-existent block silently does
  nothing.

`native_ff.py` writes the output reports directly over `hidraw`, which is why
it needs no `evdev` and works despite the driver.

## Install

Neither script has third-party dependencies:

```sh
python3 sidewinder.py read
```

## Permissions

Reading goes through `/dev/hidrawN`, force feedback through `/dev/input/eventN`.
Both are root-only by default. Either run with `sudo`, or install a udev rule so
the `input` group has access:

```sh
sudo tee /etc/udev/rules.d/99-sidewinder.rules >/dev/null <<'EOF'
KERNEL=="hidraw*", ATTRS{idVendor}=="045e", ATTRS{idProduct}=="0034", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{idVendor}=="045e", ATTRS{idProduct}=="0034", MODE="0660", GROUP="input"
EOF
sudo udevadm control --reload
sudo udevadm trigger
```

Then add yourself to the `input` group and log back in:

```sh
sudo usermod -aG input "$USER"
```

Re-run `sudo udevadm trigger` after replugging the wheel, since the hidraw node
is recreated on every enumeration.

## Usage

Read steering position, normalized to `-1.000` (full left) to `+1.000` (full right):

```sh
python3 sidewinder.py read
```

```
-0.656
-0.621
+0.032
+0.887
```

Raw values (`-512` to `511`) instead:

```sh
python3 sidewinder.py read --raw
```

Steering plus the aux axes and buttons, rate limited to 20 Hz:

```sh
python3 sidewinder.py watch --hz 20
```

```
steer  -239  norm -0.720  y 63  rz 63  buttons 0x00
steer  +267  norm +0.527  y 63  rz 63  buttons 0x00
```

Play a constant force pushing right for two seconds:

```sh
sudo python3 native_ff.py 255 2
```

Sweep the force from full-right to full-left, so you can feel the sign change:

```sh
sudo python3 native_ff.py sweep
```

Both print the reports they send, which makes it obvious when something is not
being accepted:

```
using effect block 2
  -> device control (enable actuators) 0c01
  -> set effect                 01020cff7f000003000000ff0000
  -> set constant (+255)        0502ff00
  -> effect op (start)          0a0201ff
  playing +255 for 2.0s
```

`sidewinder.py` also accepts `--device` if autodetection picks the wrong node:

```sh
python3 sidewinder.py read --device /dev/hidraw0
```

## The report format

The wheel sends report ID `1`, seven bytes:

```
   0        1         2         3         4         5        6
+--------+---------+---------+---------+---------+---------+--------+
|  0x01  | X low 8 | X hi 2  |    Y    |   Rz    | buttons | pad    |
+--------+---------+---------+---------+---------+---------+--------+
```

Steering is a 10-bit signed field. The two high bits are packed into the low
bits of byte 2, so the value must be masked to 10 bits and then sign-extended
from bit 9 rather than read as a little-endian 16-bit integer — a plain 16-bit
read turns every leftward position into a large positive number. `Y` and `Rz`
are 6-bit fields, and `0x3f` is their centre.

The HID report descriptor confirms this layout:

```
0x05 0x01        Usage Page (Generic Desktop)
0x09 0x04        Usage (Joystick)
0xa1 0x01        Collection (Application)
0x85 0x01        Report ID (1)
0x09 0x30        Usage (X)
0x16 0x00 0xfe   Logical Minimum (-512)
0x26 0xff 0x01   Logical Maximum (511)
0x75 0x0a        Report Size (10)
0x95 0x01        Report Count (1)
0x81 0x02        Input (Data, Variable, Absolute)
```

## Force feedback

Use `native_ff.py`, which writes the device's own PID output reports over
`hidraw`. The `evdev` route is a dead end on this hardware for the reasons
described above; `sidewinder.py`'s `ff` subcommand is kept only as a
demonstration of the broken path and will not produce usable force.

`native_ff.py` implements constant force. The protocol generalises to the other
effect types the device advertises (`FF_SPRING`, `FF_DAMPER`, `FF_RUMBLE`,
`FF_PERIODIC`, `FF_RAMP`, and 20 parameter blocks in total); each needs its own
report from the table in the module docstring plus the same block lifecycle.

Notes gathered while getting this working:

- The block index must be read from the device, not assumed. `native_ff.py`
  does this via feature report `2`; the load status is `1` when the block is
  ready.
- Changing an effect's magnitude needs a fresh `set effect` + `set constant`
  each time. Writing only `set constant` to a block that already played leaves
  the old force in place.
- `free_block` after stopping. Blocks are a finite pool (20 here) and are not
  reclaimed implicitly.
- `FF_GAIN` in `evdev` is not an uploadable effect — uploading one returns
  `EINVAL`. That matters only for the `sidewinder.py ff` path.

## License

MIT — see [LICENSE](LICENSE).