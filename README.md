# sidewinder-wheel

Read the steering position of a Microsoft SideWinder Force Feedback Wheel from a
Raspberry Pi (or any Linux box), and drive its force feedback.

The wheel in question: <https://de.wikipedia.org/wiki/Microsoft_SideWinder_Force_Feedback_Wheel>

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
- **Use effect block 1.** Feature report `2` advertises the block the kernel
  driver allocated, which on the unit tested is block `2` — but writing effects
  there produces no force, and neither does it for the driver's own effects.
  Block `1` is the one the device actuates. This is counter-intuitive enough
  that `native_ff.py` pins it as a constant with a comment explaining why; do
  not "fix" it by reading the feature report.

`native_ff.py` writes the output reports directly over `hidraw`, which is why
it needs no `evdev` and works despite the driver.

## Install

Neither script has third-party dependencies:

```sh
python3 sidewinder.py read
```

## Permissions

Reading and force feedback both go through `/dev/hidrawN`, which is root-only by
default. Either run with `sudo`, or install a udev rule so the `input` group has
access:

```sh
sudo tee /etc/udev/rules.d/99-sidewinder.rules >/dev/null <<'EOF'
KERNEL=="hidraw*", ATTRS{idVendor}=="045e", ATTRS{idProduct}=="0034", MODE="0660", GROUP="input"
EOF
sudo udevadm control --reload
sudo udevadm trigger
```

Only the hidraw node matters. The force-feedback path bypasses evdev entirely,
so no input-node rule is needed.

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

## Force feedback

`native_ff.py` writes the device's own PID output reports over `hidraw`. Modes:

```sh
sudo python3 native_ff.py              # centre (default): spring pulls to centre
sudo python3 native_ff.py center 10    # same, for 10 seconds
sudo python3 native_ff.py damper       # resistance proportional to turning speed
sudo python3 native_ff.py constant -150 2.0   # constant force, -255..255
sudo python3 native_ff.py sweep        # sweep right -> left, for testing
sudo python3 native_ff.py off          # stop everything, disable actuators
```

`center` and `damper` stay engaged until you run `off` (or the process exits).
Add `-v` to any mode to print the reports being sent, which makes it obvious
when something is not being accepted:

```
using effect block 1
  -> device control (enable actuators) 0c01
  -> set effect (type 8)      010108ffff00000000ff000400000000
  -> set condition            030100003f3fffff00
  -> set condition            030101003f3fffff00
  -> effect op (start)        0a010101
```

Only these modes are implemented. The device advertises more (inertia, friction,
rumble, periodic, ramp, across 20 parameter blocks); each needs its own report
plus the same block lifecycle described below.

### Notes gathered while getting this working

- Conditions (spring, damper) are written **once per axis** — two `0x03`
  reports differing only in the axis selector byte.
- Coefficients `+63/+63` and full saturation are the values known to produce
  force. They are not symmetric in the way you might expect from the PID spec.
- Changing an effect's magnitude needs a fresh `set effect` + `set constant`
  each time. Writing only `set constant` to a block that already played leaves
  the old force in place.
- `free_block` after stopping. Blocks are a finite pool (20 here) and are not
  reclaimed implicitly.
- The device's value ranges are much smaller than the PID spec's: constant force
  is `-255..255` rather than `-10000..10000`, and spring coefficients are signed
  bytes.

`sidewinder.py` also accepts `--device` if autodetection picks the wrong node:

```sh
python3 sidewinder.py read --device /dev/hidraw0
```

## MCP server

`mcp_server.py` exposes the wheel to any MCP client over HTTP, so an agent can
read the steering and drive the force feedback without shell access.

```sh
sudo python3 mcp_server.py --host 127.0.0.1 --port 8765
```

Tools:

| Tool | Purpose |
|---|---|
| `get_status` | Is the wheel present, and which node |
| `read_position` | One steering reading |
| `read_inputs` | Steering, both pedals and the buttons |
| `watch_position` | Sample position over a window, with min/max/span |
| `set_center` | Centring spring, optional duration and strength |
| `set_damper` | Damper, optional duration and strength |
| `play_constant` | Constant force, `-255..255`, for a duration |
| `sweep` | Sweep the full force range |
| `stop` | Stop all effects, release the wheel |

### Client configuration

```json
{
  "mcp": {
    "sidewinder-wheel": {
      "type": "remote",
      "url": "http://wheel-host:8765/mcp"
    }
  }
}
```

If the server requires a token, add
`"headers": {"Authorization": "Bearer YOUR_TOKEN"}`.

### Security

Force feedback moves the wheel under its own power, so an open port lets anyone
who can reach it do that. Accordingly:

- The server binds to `127.0.0.1` by default.
- Binding to any other address requires a token (`--token`, or the
  `WHEEL_MCP_TOKEN` environment variable) unless you pass `--insecure`.

There is no TLS. On a trusted LAN, a token over plain HTTP is reasonable; for
anything else, put it behind a reverse proxy or tunnel.

### Pedals and buttons

`read_inputs` returns steering, the two pedal axes and the button set:

```json
{
  "normalized": 0.403,
  "raw": 412,
  "pedals": {"y": 1.0, "rz": 0.238},
  "buttons": ["button1"],
  "buttons_raw": 1
}
```

`raw` is the 10-bit signed steering value (-512 full left). Pedal values are
fractions: `0.0` released, `1.0` fully pressed.

### Running as a service

`sidewinder-wheel-mcp.service` is a systemd unit. Install the scripts and the
unit, then enable it:

```sh
sudo install -d /usr/local/lib/sidewinder-wheel
sudo install -m644 wheelctl.py native_ff.py sidewinder.py mcp_server.py \
    /usr/local/lib/sidewinder-wheel/
sudo install -m644 sidewinder-wheel-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sidewinder-wheel-mcp
```

The unit runs as root because the wheel's hidraw node is root-only by default.
Install the udev rule below and drop `User=root` from the unit to run it
unprivileged instead.

One hardening trap, found the hard way: listing any `DeviceAllow=` entry turns
the device cgroup into a whitelist, so the hidraw class must be named
explicitly. `DeviceAllow=char-usb_device` does not cover it — the wheel's node
is a plain char device, and the failure surfaces as `Operation not permitted` on
`open()`, not as a startup error.

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
read turns every leftward position into a large positive number.

`Y` and `Rz` are the **two pedals**, as 6-bit fields resting at `0x3f`. They
sweep independently down towards 0 as each pedal is pressed, so they are already
proportional: no separate button mapping is involved. The resolution is only
64 steps, which is a limitation of the hardware, not of the decoding. Note that
byte 6 reads `0x01` on the unit tested and does not change; it is not a button.

Byte 5 carries the buttons, **one bit per button**. Six bits were observed to
change (`0x01, 0x02, 0x04, 0x08, 0x10, 0x20`); the descriptor declares eight.
`wheelctl.button_names()` decodes them to `button1`..`button8`.

### The wheel only reports on change

This matters for anything built on top of it. The wheel sends **no periodic
reports**: leave it alone and the interrupt endpoint is silent, so a read with a
short timeout legitimately returns "nothing". Every sample must be provoked by
moving a control, and a fresh `open()` + `read()` pair can miss reports that
arrived while nobody was listening. If you need a stream, hold the descriptor
open and read continuously rather than polling with short timeouts.

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

## License

MIT — see [LICENSE](LICENSE).