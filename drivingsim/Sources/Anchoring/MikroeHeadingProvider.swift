import ARKit
import Foundation
import GameController
import Logging
import Observation
import RealityKit
import simd

/// Derives a heading from a MIKROE Spatial Anchor accessory (R1 / S1).
///
/// This is the physical-anchor path: the car's front is aligned to the puck's
/// forward axis, flattened onto the floor so a tilted or face-up puck cannot
/// tip the world. It needs a paired accessory and visionOS 27, neither of which
/// a simulator has, so `ManualHeadingProvider` is the fallback.
///
/// The accessory API is gated exactly as the Innoactive Spatial app gates it —
/// `#if compiler(>=6.4)` plus `#available(visionOS 27.0, *)` — because a 26.5
/// build compiles this away silently and the tracker then simply never appears.
@MainActor
@Observable
final class MikroeHeadingProvider: AnchorHeadingProvider {
    nonisolated let name = "MIKROE Spatial Anchor"

    private let logger = Logger(label: "drivingsim.MikroeHeadingProvider")

    var yaw: Float?
    var detail: String = "Not started"

    private var session: ARKitSession?
    #if compiler(>=6.4)
    private var provider: AccessoryTrackingProvider?
    #endif
    private var updateTask: Task<Void, Never>?
    private var eventTask: Task<Void, Never>?

    /// Both R1 and S1 advertise this as their Bluetooth name. The filter keeps a
    /// paired stylus from being taken for the room referent.
    nonisolated static let allowedNameFragments = ["Spatial Anchor"]

    func start() {
        #if compiler(>=6.4)
        if #available(visionOS 27.0, *) {
            Task { await self.beginTracking() }
            return
        }
        #endif
        detail = "Built without the visionOS 27 SDK"
        logger.warning("MIKROE anchoring unavailable: \(detail)")
    }

    func stop() {
        updateTask?.cancel()
        updateTask = nil
        eventTask?.cancel()
        eventTask = nil
        // Cancelling is not stopping: the session owns the accessory and would
        // otherwise keep it running, which is the wedge condition the main app
        // documents. Tasks first, then the session.
        session?.stop()
        session = nil
        #if compiler(>=6.4)
        provider = nil
        #endif
        yaw = nil
    }

    #if compiler(>=6.4)
    @available(visionOS 27.0, *)
    private func beginTracking() async {
        do {
            let accessories = await Self.loadWhitelistedAccessories()
            guard !accessories.isEmpty else {
                detail = "No paired Spatial Anchor accessory found"
                logger.info("MIKROE anchoring: \(detail)")
                return
            }

            let session = ARKitSession()
            let provider = AccessoryTrackingProvider(accessories: accessories)

            let authorization = await session.requestAuthorization(for: [.accessoryTracking])
            guard authorization[.accessoryTracking] == .allowed else {
                detail = "Accessory tracking not authorised"
                return
            }

            self.session = session
            self.provider = provider
            try await session.run([provider])
            detail = "Tracking \(accessories.count) accessory(ies)"

            startReadingPoses(provider)
            monitorEvents(session)
        } catch {
            detail = "Failed to start: \(error.localizedDescription)"
            logger.error("MIKROE anchoring: \(detail)")
        }
    }

    @available(visionOS 27.0, *)
    private static func whitelistedAccessories() -> [GCSpatialAccessory] {
        GCSpatialAccessory.spatialAccessories.filter { device in
            let name = device.vendorName ?? ""
            return allowedNameFragments.contains {
                name.localizedCaseInsensitiveContains($0)
            }
        }
    }

    /// Load reference accessories for the whitelisted devices.
    ///
    /// `GCSpatialAccessory` is not Sendable, so it cannot be carried across the
    /// `await` in `Accessory(device:)` from the main actor. Filtering and
    /// loading inside one nonisolated call keeps each device on a single
    /// isolation domain, and only the resulting `Accessory` values — which the
    /// ARKit provider is designed to accept — come back.
    @available(visionOS 27.0, *)
    private nonisolated static func loadWhitelistedAccessories() async -> [Accessory] {
        var loaded: [Accessory] = []
        for device in GCSpatialAccessory.spatialAccessories {
            let name = device.vendorName ?? ""
            guard allowedNameFragments.contains(where: {
                name.localizedCaseInsensitiveContains($0)
            }) else { continue }
            do {
                loaded.append(try await Accessory(device: device))
            } catch {
                continue
            }
        }
        return loaded
    }

    @available(visionOS 27.0, *)
    private func startReadingPoses(_ provider: AccessoryTrackingProvider) {
        updateTask?.cancel()
        updateTask = Task { [weak self] in
            // `anchorUpdates` rather than `latestAnchors`: the latter is
            // documented AR_MT_UNSAFE and lower accuracy.
            for await update in provider.anchorUpdates {
                guard !Task.isCancelled, let self else { break }
                guard update.event != .removed else { continue }
                let transform = update.anchor.originFromAnchorTransform
                let yaw = Self.floorParallelYaw(from: transform)
                self.yaw = yaw
                self.detail = "Heading \(Int(yaw * 180 / .pi))°"
            }
        }
    }

    @available(visionOS 27.0, *)
    private func monitorEvents(_ session: ARKitSession) {
        eventTask?.cancel()
        eventTask = Task { [weak self] in
            for await event in session.events {
                guard !Task.isCancelled, let self else { break }
                guard case let .dataProviderStateChanged(_, newState, error) = event else { continue }
                if let error {
                    self.detail = "Provider \(newState): \(error.localizedDescription)"
                } else {
                    self.detail = "Provider \(newState)"
                }
            }
        }
    }
    #endif

    /// Floor-parallel yaw from an accessory transform.
    ///
    /// Mirrors `TrackerPoseMath.floorParallelPose`: project the forward axis
    /// onto the horizontal plane, and when forward is near-vertical derive the
    /// heading from the right axis instead, so a puck lying face-up still gives
    /// a usable direction.
    static func floorParallelYaw(from matrix: simd_float4x4) -> Float {
        let forward = SIMD3<Float>(matrix.columns.2.x, matrix.columns.2.y, matrix.columns.2.z)
        let horizontal: SIMD3<Float>
        if abs(forward.y) <= 0.9, simd_length(SIMD2<Float>(forward.x, forward.z)) > 1e-4 {
            horizontal = SIMD3<Float>(forward.x, 0, forward.z)
        } else {
            let right = SIMD3<Float>(matrix.columns.0.x, matrix.columns.0.y, matrix.columns.0.z)
            guard simd_length(SIMD2<Float>(right.x, right.z)) > 1e-4 else { return 0 }
            horizontal = SIMD3<Float>(-right.z, 0, right.x)
        }
        let unit = simd_normalize(horizontal)
        return atan2(unit.x, unit.z)
    }
}