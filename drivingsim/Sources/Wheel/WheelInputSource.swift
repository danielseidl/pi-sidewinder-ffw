import Foundation
import Logging
import Observation

/// Polls the wheel for control state and publishes it.
///
/// The wheel sends **nothing at all** unless a control changes, which drives
/// the shape of this loop: each `read_inputs` call carries its own timeout and
/// routinely returns "no report arrived". That is a normal outcome, not an
/// error, so the last known state is kept and republished rather than being
/// reset to neutral — resetting would make the car twitch to centre every time
/// the driver holds still.
@MainActor
@Observable
final class WheelInputSource {
    private let logger = Logger(label: "drivingsim.WheelInputSource")
    private let client = WheelMCPClient()

    var configuration: WheelMCPClient.Configuration = .default
    var connected = false
    var lastError: String?
    var inputs: WheelInputs = .neutral
    var swapsPedals = false

    private var pump: Task<Void, Never>?

    func start() {
        guard pump == nil else { return }
        pump = Task { [weak self] in
            await self?.run()
        }
    }

    func stop() {
        pump?.cancel()
        pump = nil
    }

    private func run() async {
        // 2 s per call: long enough to usually catch a report when the driver is
        // moving, short enough that a dead endpoint is noticed promptly. It is
        // not a correctness gate — a miss simply republishes the last state.
        let readTimeout = 2.0

        while !Task.isCancelled {
            do {
                let result = try await client.call(
                    "read_inputs",
                    arguments: ["timeout": readTimeout],
                    configuration: configuration)

                if connected == false {
                    logger.info("Wheel connected at \(configuration.displayName)")
                }
                connected = true
                lastError = nil

                if !result.isError, let parsed = WheelInputs.parse(result.text) {
                    inputs = parsed
                }
                // An isError result here means "no change reported", so the
                // previous state deliberately stands.
            } catch {
                if connected || lastError == nil {
                    logger.warning("Wheel read failed: \(error.localizedDescription)")
                }
                connected = false
                lastError = error.localizedDescription
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
}