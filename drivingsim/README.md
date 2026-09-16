# drivingsim

A visionOS first-person driving sample that uses the SideWinder Force Feedback
Wheel as its controller, driven over the MCP server in the parent directory.

This exists to exercise the wheel end to end — steering, pedals, buttons and
force feedback — against something that reacts visibly, rather than by reading
report bytes by hand.

## Requirements

- Xcode 27 with the visionOS 27 SDK. `XROS_DEPLOYMENT_TARGET` is 27.0, and the
  MIKROE accessory path is gated on `#if compiler(>=6.4)`; a 26.5 build compiles
  it away silently and the tracker then simply never appears.
- A Vision Pro running visionOS 27.0 or later, to run it on device.
- The wheel's MCP server reachable at the configured host and port.

## Build and run

The Xcode project is generated:

```sh
xcodegen generate
open drivingsim.xcodeproj
```

Command line, simulator:

```sh
../scripts/require-xcode27.sh xcodebuild build \
  -project drivingsim.xcodeproj -scheme drivingsim \
  -destination 'platform=visionOS Simulator,name=Apple Vision Pro'
```

Check the produced binary rather than trusting `BUILD SUCCEEDED`:

```sh
xcrun vtool -show-build <app>/drivingsim | grep -E 'minos|sdk'   # want sdk 27.x
```

## Controls

The app takes input from the first of these that is available:

1. **The wheel**, over MCP, when the server answers.
2. **A gamepad** (extended profile) — a Vision Pro has no keyboard, so this is
   what makes the sim drivable from inside the headset when the wheel is not
   connected.
3. **A scripted driver**, only when `DRIVINGSIM_SCRIPTED=1` is set, for
   automated runs.

| Control | Action |
|---|---|
| Steering | Turn the car |
| `y` pedal | Throttle |
| `rz` pedal | Brake |
| Button A / `button1` | Reset car |
| Button B / `button2` | Toggle heading source |
| Button Y / `button4` | Reset car |
| Shoulder / `button6` | Handbrake |

Which physical pedal is which is wiring, not protocol, so the app has a
`swapsPedals` setting rather than a hard assumption.

## Heading from the MIKROE spatial anchor

The car's front is aligned to the forward axis of a paired MIKROE Spatial
Anchor R1/S1 puck, flattened onto the floor so a tilted or face-up puck cannot
tip the world. This mirrors `TrackerPoseMath.floorParallelPose` from the
Innoactive Spatial app.

The accessory API needs visionOS 27 **and a physically paired accessory**.
Neither exists on a simulator, so `ManualHeadingProvider` is the fallback and
the app remains runnable and testable everywhere. The Connection panel names
which provider is live and reports why, e.g. "No paired Spatial Anchor
accessory found".

## Force feedback

Driven from the simulation state:

- A centring spring while driving, so the wheel resists leaving centre.
- Speed-scaled damper.
- A constant-force kick on collision.
- Everything stopped on teardown.

Effects are commanded on transitions only. Nothing reports back whether the
motors actually moved, so re-commanding the same effect every frame would flood
the server for no benefit.

## Automated runs

```sh
SIMCTL_CHILD_DRIVINGSIM_SCRIPTED=1 \
SIMCTL_CHILD_DRIVINGSIM_AUTODRIVE=1 \
xcrun simctl launch <device> de.innoactive.drivingsim
```

`AUTODRIVE` enters the immersive space without a button press (there is no tap
injection in `simctl`), and `SCRIPTED` feeds synthetic throttle and steering so
the vehicle loop, collisions and HUD can be observed headlessly.

## What is verified, and what is not

Verified on the visionOS 27.0 simulator:

- Builds against the 27.0 SDK (`minos 27.0 / sdk 27.0`).
- The immersive space opens, the world renders, and the car drives, steers and
  collides; the HUD tracks speed and input source.
- The MCP client attempts the endpoint and reports a transport failure cleanly
  when the server is absent.
- The MIKROE provider starts, enumerates accessories, finds none, and says so.

Not verified here:

- **The wheel over MCP.** The Pi host was down (its USB supply could not
  sustain the wheel's motors), so no end-to-end run against real hardware has
  happened. The client code path is exercised only as far as connecting.
- **The MIKROE anchor on real hardware.** It needs a paired R1/S1; the logic is
  implemented and gated, but no device run has confirmed the pose.
- Force feedback reaching the motors from this app.