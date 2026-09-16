import Foundation

/// One decoded state of the wheel's controls.
///
/// Mirrors the `read_inputs` tool: steering is -1 (full left) .. +1 (full
/// right); each pedal is 0 (released) .. 1 (fully pressed); buttons is the set
/// of pressed names ("button1".."button8").
struct WheelInputs: Sendable, Equatable {
    var steering: Double
    var pedalY: Double
    var pedalRz: Double
    var buttons: Set<String>
    var raw: Int

    static let neutral = WheelInputs(steering: 0, pedalY: 0, pedalRz: 0,
                                     buttons: [], raw: 0)

    /// Which pedal is which is a property of the physical wiring, not of the
    /// report: on the unit tested `y` falls away first under the right-hand
    /// pedal. Exposed as a setting rather than assumed.
    func throttle(swapped: Bool) -> Double {
        swapped ? pedalRz : pedalY
    }

    func brake(swapped: Bool) -> Double {
        swapped ? pedalY : pedalRz
    }

    func isPressed(_ button: String) -> Bool {
        buttons.contains(button)
    }
}

extension WheelInputs {
    /// Parse the JSON body of a `read_inputs` result.
    static func parse(_ text: String) -> WheelInputs? {
        guard let data = text.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        let steering = doubleValue(root["normalized"]) ?? 0
        let raw = intValue(root["raw"]) ?? 0
        let pedals = root["pedals"] as? [String: Any] ?? [:]
        let pedalY = doubleValue(pedals["y"]) ?? 0
        let pedalRz = doubleValue(pedals["rz"]) ?? 0
        let buttons = Set((root["buttons"] as? [String]) ?? [])

        return WheelInputs(steering: steering, pedalY: pedalY, pedalRz: pedalRz,
                           buttons: buttons, raw: raw)
    }

    private static func doubleValue(_ any: Any?) -> Double? {
        if let value = any as? Double { return value }
        if let value = any as? Int { return Double(value) }
        if let value = any as? NSNumber { return value.doubleValue }
        return nil
    }

    private static func intValue(_ any: Any?) -> Int? {
        if let value = any as? Int { return value }
        if let value = any as? Double { return Int(value) }
        return nil
    }
}