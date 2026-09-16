import Foundation
import simd

/// Supplies the world heading the car should start facing.
///
/// The sim's world is built once, in its own frame; the anchor's job is to
/// orient that frame so the car's front points along a real-world direction.
/// Callers only ever need a yaw.
@MainActor
protocol AnchorHeadingProvider: AnyObject {
    var name: String { get }
    var yaw: Float? { get }
    var detail: String { get }
    func start()
    func stop()
}

/// Shared storage for the simple providers, which hold no ARKit state.
@MainActor
@Observable
class StoredHeadingProvider: AnchorHeadingProvider {
    nonisolated let name: String
    var yaw: Float?
    var detail: String

    init(name: String, yaw: Float? = nil, detail: String) {
        self.name = name
        self.yaw = yaw
        self.detail = detail
    }

    func start() {}
    func stop() {}
}