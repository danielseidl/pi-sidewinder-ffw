import Foundation
import Logging
import Observation

/// Drives the wheel's force feedback from the simulation.
///
/// Force feedback has no inverse channel: the server accepts commands and
/// returns an acknowledgement, but nothing reports whether the motors actually
/// moved. So effect changes are sent on transitions only — commanding a
/// centring spring every poll would flood the server for no benefit, and the
/// device would be re-declaring the same effect sixty times a second.
@MainActor
@Observable
final class WheelFeedbackSink {
    enum Mode: String, Sendable {
        case off
        case center
        case damper
    }

    private let logger = Logger(label: "drivingsim.WheelFeedbackSink")
    private let client = WheelMCPClient()

    var configuration: WheelMCPClient.Configuration = .default
    var enabled = false
    var mode: Mode = .center
    var lastCommand: String?
    var lastError: String?

    private var appliedMode: Mode = .off
    private var appliedEnabled = false
    private var transientTask: Task<Void, Never>?

    /// Reconcile the wheel with the requested state. Cheap to call often: it
    /// no-ops unless something actually changed.
    func update(steering: Double, speed: Double) async {
        guard enabled else {
            if appliedEnabled {
                await send("stop", arguments: [:])
                appliedEnabled = false
                appliedMode = .off
            }
            return
        }

        if !appliedEnabled {
            appliedEnabled = true
            appliedMode = .off
        }

        if appliedMode != mode {
            await apply(mode: mode, speed: speed)
            appliedMode = mode
        }
    }

    private func apply(mode: Mode, speed: Double) async {
        switch mode {
        case .off:
            await send("stop", arguments: [:])
        case .center:
            await send("set_center", arguments: ["strength": 63])
        case .damper:
            // Heavier resistance with speed, which is the closest this device
            // offers to a road-feel proxy.
            let strength = max(20, min(127, Int(40 + speed * 3)))
            await send("set_damper", arguments: ["strength": strength])
        }
    }

    /// A short, strong kick — used for collisions.
    func impulse(level: Int, seconds: Double) async {
        guard enabled else { return }
        transientTask?.cancel()
        let task = Task { [weak self] in
            guard let self else { return }
            await self.send("play_constant", arguments: ["level": level, "seconds": seconds])
            // Restore the steady-state effect once the kick finishes.
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self.appliedMode = .off
            await self.apply(mode: self.mode, speed: 0)
            self.appliedMode = self.mode
        }
        transientTask = task
    }

    func release() async {
        transientTask?.cancel()
        transientTask = nil
        await send("stop", arguments: [:])
        appliedEnabled = false
        appliedMode = .off
    }

    private func send(_ tool: String, arguments: [String: any Sendable]) async {
        do {
            let result = try await client.call(tool, arguments: arguments,
                                               configuration: configuration)
            lastCommand = "\(tool)\(arguments.isEmpty ? "" : " \(arguments)")"
            lastError = result.isError ? result.text : nil
        } catch {
            lastError = error.localizedDescription
            logger.debug("Feedback \(tool) failed: \(error.localizedDescription)")
        }
    }
}