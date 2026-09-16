import Foundation

/// The heading source used when no physical anchor is available.
///
/// A simulator has no Bluetooth accessory, and a headset without a paired
/// MIKROE puck has nothing to track, so without this the app could not be run
/// or tested anywhere except a fully-equipped device.
@MainActor
final class ManualHeadingProvider: StoredHeadingProvider {
    init(yaw: Float = 0) {
        super.init(name: "Manual heading", yaw: yaw,
                   detail: "Set by hand (no anchor accessory)")
    }
}