# sidewinder-wheel

Read the steering position of a Microsoft SideWinder Force Feedback Wheel from a
Raspberry Pi (or any Linux box), and drive its force feedback.

Tested on a Raspberry Pi 5 running Debian 13 with kernel 6.18, against the USB
SideWinder Force Feedback Wheel (`045e:0034`).

## Why this is not just an evdev joystick

The kernel supports this wheel out of the box. `hid-generic` and `hid-pidff`
bind it, an input node appears, and it advertises the full HID PID force
feedback feature set. `evdev` sees the device and its `ABS_X` axis.

On the unit tested, however, that input node never produced a single event:
no axis movement, no button presses, not even a `SYN_REPORT`. Reading the same
device through `hidraw` returned reports immediately, and force feedback played
correctly through `evdev`. So the wheel was transmitting on the interrupt IN
endpoint the whole time while the kernel's input layer delivered nothing.

Rather than depend on the input node, this tool reads the wheel from `hidraw`
directly. If your unit does deliver evdev events, `evdev` will work fine too —
this approach is simply the one that was verified to work.

## Install

The script has no dependencies for reading:

```sh
python3 sidewinder.py read
```

Force feedback needs the `evdev` package:

```sh
python3 -m venv ~/.venv
~/.venv/bin/pip install evdev
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

Play a constant force pushing right for one second:

```sh
python3 sidewinder.py ff play --level 0.7 --ms 1000 --gain 90
```

Interactively try the different effect types (`a` spring toggle, `l`/`r` push
left/right, `f` rumble, `c` stop, `q` quit):

```sh
python3 sidewinder.py ff demo
```

Both subcommands accept `--device` if autodetection picks the wrong node:

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

Force feedback works through `evdev` against the input node, which advertises
`FF_CONSTANT`, `FF_SPRING`, `FF_DAMPER`, `FF_FRICTION`, `FF_INERTIA`,
`FF_PERIODIC`, `FF_RUMBLE`, `FF_RAMP` and `FF_AUTOCENTER`.

Two details worth knowing, both found the hard way:

- `FF_GAIN` is not an uploadable effect. Uploading one returns `EINVAL`; write
  it with `EV_FF`/`FF_GAIN` directly.
- In the `evdev` Python bindings the `ff.Effect` members are plain `ctypes`
  structs, so the constructor takes no device argument — allocate with
  `ff.Effect()`, assign the fields, then call `upload_effect`.

## License

MIT — see [LICENSE](LICENSE).