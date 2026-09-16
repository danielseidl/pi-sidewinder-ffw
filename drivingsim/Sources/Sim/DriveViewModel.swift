import Foundation
import Logging
import Observation
import RealityKit
import simd

/// Owns the simulation and connects the wheel, the gamepad, the anchor and the
/// force-feedback sink.
@MainActor
@Observable
final class DriveViewModel {
    private let logger = Logger(label: "drivingsim.DriveViewModel")

    let wheelInputs = WheelInputSource()
    let gamepad = GamepadInputSource()
    let feedback = WheelFeedbackSink()

    var anchoredHeading: AnchorHeadingProvider

    var vehicle = VehicleModel()
    var state = VehicleModel.State()
    var world = SimWorld.Props()

    var statusMessage = "Ready"
    var collisionCount = 0
    var lastCollisionAt: Date?

    private var tickTask: Task<Void, Never>?
    private var scriptedTask: Task<Void, Never>?
    private var scriptedInputs: WheelInputs?
    private var lastTick = ContinuousClock.now
    private var useAnchorHeading = true

    init() {
        anchoredHeading = MikroeHeadingProvider()
    }

    var speedKPH: Int { Int(abs(state.speed) * 3.6) }
    var steeringDisplay: Double { Double(state.steer / vehicle.maxSteer) }

    /// The wheel is authoritative when the MCP server answers; the gamepad is
    /// the fallback so the sim stays drivable with no Pi and no wheel.
    var activeInputs: WheelInputs {
        if let scriptedInputs { return scriptedInputs }
        return wheelInputs.connected ? wheelInputs.inputs : gamepad.inputs
    }

    var inputSourceName: String {
        if scriptedInputs != nil { return "Scripted" }
        if wheelInputs.connected { return "Wheel (MCP)" }
        if gamepad.connected { return "Gamepad" }
        return "None"
    }

    func start() {
        world = SimWorld.build()
        applyAnchorHeading()
        wheelInputs.start()
        gamepad.start()
        startTicking()
    }

    func stop() {
        tickTask?.cancel()
        tickTask = nil
        wheelInputs.stop()
        gamepad.stop()
        anchoredHeading.stop()
        Task { await feedback.release() }
    }

    func switchHeadingProvider() {
        useAnchorHeading.toggle()
        applyAnchorHeading()
    }

    private func applyAnchorHeading() {
        anchoredHeading.stop()
        anchoredHeading = useAnchorHeading
            ? MikroeHeadingProvider()
            : ManualHeadingProvider()
        anchoredHeading.start()
        // Apply the anchor's yaw to the car so its front faces along the anchor.
        // A manual provider holds this at 0 until the user changes it.
        if let yaw = anchoredHeading.yaw {
            state.heading = yaw
        }
        statusMessage = "Heading: \(anchoredHeading.name)"
    }

    func reset() {
        state = VehicleModel.State(heading: anchoredHeading.yaw ?? 0)
        collisionCount = 0
        statusMessage = "Reset"
    }

    /// Drive the car from a fixed script, ignoring the wheel and gamepad.
    ///
    /// Only for automated runs (`DRIVINGSIM_SCRIPTED=1`): it makes the vehicle
    /// loop, the collision path and the HUD observable headlessly, where no
    /// hands are available to press a button or turn a wheel.
    func runScriptedDriver() {
        scriptedInputs = WheelInputs.neutral
        scriptedTask?.cancel()
        scriptedTask = Task { [weak self] in
            var elapsed: Double = 0
            while !Task.isCancelled {
                guard let self else { break }
                elapsed += 0.1
                // Straight, then a long left sweep, then right — enough to
                // leave the start area and meet an obstacle.
                var inputs = WheelInputs.neutral
                inputs.pedalY = 0.85
                inputs.steering = sin(elapsed / 4) * 0.8
                self.scriptedInputs = inputs
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
        statusMessage = "Scripted driver active"
    }

    private func startTicking() {
        tickTask?.cancel()
        lastTick = ContinuousClock.now
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { break }
                self.tick()
                try? await Task.sleep(for: .milliseconds(16))
            }
        }
    }

    private func tick() {
        let now = ContinuousClock.now
        let elapsed = lastTick.duration(to: now)
        lastTick = now
        let dt = min(0.05, Float(elapsed.components.seconds) +
                     Float(elapsed.components.attoseconds) / 1e18)

        let inputs = activeInputs
        let throttle = Float(inputs.throttle(swapped: wheelInputs.swapsPedals))
        let brake = Float(inputs.brake(swapped: wheelInputs.swapsPedals))
        let handbrake = inputs.isPressed("button6")

        _ = vehicle.step(state: &state,
                         throttle: throttle,
                         brake: brake,
                         steerInput: Float(inputs.steering),
                         handbrake: handbrake,
                         dt: dt)

        if inputs.isPressed("button4") {
            reset()
        }

        handleCollisions()
        syncFeedback()
    }

    private func handleCollisions() {
        guard let obstacle = SimWorld.collision(at: state.position, in: world) else { return }

        collisionCount += 1
        lastCollisionAt = Date()
        statusMessage = "Collision!"

        // Bounce off and kill most of the speed.
        let delta = state.position - obstacle
        let away = simd_length(SIMD2<Float>(delta.x, delta.z)) > 1e-4
            ? simd_normalize(SIMD3<Float>(delta.x, 0, delta.z))
            : SIMD3<Float>(0, 0, 1)
        state.position = obstacle + away * (SimWorld.obstacleRadius + SimWorld.carRadius + 0.05)
        state.speed *= -0.25

        let level = Int(max(60, min(255, abs(state.speed) * 12)))
        Task { await feedback.impulse(level: level, seconds: 0.35) }
    }

    private func syncFeedback() {
        feedback.configuration = wheelInputs.configuration
        feedback.enabled = wheelInputs.connected
        Task { await feedback.update(steering: Double(state.steer),
                                     speed: Double(abs(state.speed))) }
    }
}