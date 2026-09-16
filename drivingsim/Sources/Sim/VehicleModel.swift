import Foundation
import simd

/// A readable arcade vehicle model.
///
/// Not a physics simulation: it is tuned to feel right from the driver's seat
/// and to respond visibly to the wheel, which is the point of the sample. The
/// bicycle-style geometry is nominal.
struct VehicleModel: Sendable {
    struct State: Sendable {
        var position: SIMD3<Float> = .zero
        var heading: Float = 0
        var speed: Float = 0
        var steer: Float = 0
    }

    var maxSpeed: Float = 28
    var maxReverse: Float = 6
    var enginePower: Float = 14
    var brakePower: Float = 26
    var rollingDrag: Float = 0.6
    var maxSteer: Float = 0.62
    var steerRate: Float = 3.2
    var wheelbase: Float = 2.6

    /// Advance one step.
    ///
    /// `throttle` and `brake` are 0..1, `steerInput` is -1..1. `handbrake`
    /// forces a spin so the driver can provoke a slide deliberately.
    func step(state: inout State, throttle: Float, brake: Float, steerInput: Float,
              handbrake: Bool, dt: Float) -> Float {
        let target = max(-1, min(1, steerInput)) * maxSteer
        let rate = steerRate * dt
        if target > state.steer {
            state.steer = min(target, state.steer + rate)
        } else {
            state.steer = max(target, state.steer - rate)
        }

        let speedFraction = min(1, abs(state.speed) / maxSpeed)
        let effectiveSteer = state.steer * (1 - 0.55 * speedFraction)

        state.speed += (throttle - brake) * (throttle > 0 ? enginePower : brakePower) * dt

        if handbrake {
            state.speed -= state.speed * 3.5 * dt
        }

        let drag = rollingDrag * state.speed * dt
        state.speed -= drag
        if !handbrake && throttle < 0.01 && brake < 0.01 && abs(state.speed) < 0.35 {
            state.speed = 0
        }
        state.speed = max(-maxReverse, min(maxSpeed, state.speed))

        if abs(state.speed) > 0.01 {
            let yawRate = state.speed / wheelbase * tan(effectiveSteer)
            state.heading += yawRate * dt * (handbrake ? 1.8 : 1)
        }

        let forward = SIMD3<Float>(sin(state.heading), 0, cos(state.heading))
        state.position += forward * state.speed * dt

        return state.speed
    }
}