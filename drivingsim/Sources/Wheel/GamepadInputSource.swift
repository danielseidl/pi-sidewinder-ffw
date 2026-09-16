import Foundation
import GameController
import Logging
import Observation

/// Carries a non-Sendable value across an isolation hop where the caller
/// already knows the value is confined to the main actor.
private struct UncheckedSendable<T>: @unchecked Sendable {
    let value: T
    init(_ value: T) { self.value = value }
}

/// Reads a connected gamepad and presents it as `WheelInputs`.
///
/// A Vision Pro has no keyboard, so without this the sim could not be driven
/// from inside the headset unless the wheel's server is reachable. It feeds the
/// same `WheelInputs` shape the MCP client produces, so the drivetrain does not
/// care which one is live.
@MainActor
@Observable
final class GamepadInputSource {
    private let logger = Logger(label: "drivingsim.GamepadInputSource")

    var connected = false
    var controllerName: String?
    var inputs: WheelInputs = .neutral

    private var controller: GCController?
    private var observers: [any NSObjectProtocol] = []

    func start() {
        // `object: nil` and the notification's own queue, then hop explicitly:
        // GCController is not Sendable, so capturing it inside a MainActor
        // closure that the notification centre might invoke elsewhere is what
        // the strict-concurrency checker rejects.
        let connect = NotificationCenter.default.addObserver(
            forName: .GCControllerDidConnect, object: nil, queue: nil
        ) { note in
            guard let controller = note.object as? GCController else { return }
            let box = UncheckedSendable(controller)
            Task { @MainActor [weak self] in
                self?.attach(box.value)
            }
        }
        let disconnect = NotificationCenter.default.addObserver(
            forName: .GCControllerDidDisconnect, object: nil, queue: nil
        ) { [weak self] _ in
            Task { @MainActor in
                self?.detach()
            }
        }
        observers = [connect, disconnect]

        GCController.startWirelessControllerDiscovery {}
        if let existing = GCController.controllers().first {
            attach(existing)
        }
    }

    func stop() {
        for observer in observers {
            NotificationCenter.default.removeObserver(observer)
        }
        observers = []
        controller?.extendedGamepad?.valueChangedHandler = nil
        controller = nil
        connected = false
        controllerName = nil
    }

    private func attach(_ controller: GCController) {
        self.controller = controller
        connected = true
        controllerName = controller.vendorName

        controller.extendedGamepad?.valueChangedHandler = { [weak self] pad, _ in
            MainActor.assumeIsolated {
                self?.ingest(pad)
            }
        }
        logger.info("Gamepad connected: \(controller.vendorName ?? "unknown")")
    }

    private func detach() {
        controller = nil
        connected = false
        controllerName = nil
        inputs = .neutral
        logger.info("Gamepad disconnected")
    }

    private func ingest(_ pad: GCExtendedGamepad) {
        var next = WheelInputs.neutral
        next.steering = Double(pad.leftThumbstick.xAxis.value)
        next.pedalY = Double(pad.rightTrigger.value)
        next.pedalRz = Double(pad.leftTrigger.value)

        var buttons: Set<String> = []
        if pad.buttonA.isPressed { buttons.insert("button1") }
        if pad.buttonB.isPressed { buttons.insert("button2") }
        if pad.buttonX.isPressed { buttons.insert("button3") }
        if pad.buttonY.isPressed { buttons.insert("button4") }
        if pad.leftShoulder.isPressed { buttons.insert("button5") }
        if pad.rightShoulder.isPressed { buttons.insert("button6") }
        next.buttons = buttons

        inputs = next
    }
}