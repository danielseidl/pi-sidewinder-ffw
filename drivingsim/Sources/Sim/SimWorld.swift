import Foundation
import RealityKit
import simd

/// The props the car drives among, plus the collision test against them.
///
/// Everything is generated from primitives so the sample carries no asset files
/// and has no licensing questions.
struct SimWorld {
    @MainActor
    struct Props {
        var root = Entity()
        var obstacles: [SIMD3<Float>] = []
        var gateCentres: [SIMD3<Float>] = []
    }

    static let obstacleRadius: Float = 1.6
    static let carRadius: Float = 1.3

    /// Build the ground, a ring of gates, and scattered obstacles.
    @MainActor
    static func build() -> Props {
        var props = Props()

        let ground = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(400, 0.2, 400)),
            materials: [SimpleMaterial(color: .init(red: 0.16, green: 0.19, blue: 0.24, alpha: 1),
                                       isMetallic: false)])
        ground.position = SIMD3<Float>(0, -0.1, 0)
        props.root.addChild(ground)

        // Lane markings radiating from the origin, so motion reads visually.
        for index in 0..<16 {
            let angle = Float(index) / 16 * 2 * .pi
            let dash = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(0.35, 0.02, 24)),
                materials: [SimpleMaterial(color: .init(red: 0.85, green: 0.85, blue: 0.5, alpha: 1),
                                           isMetallic: false)])
            dash.position = SIMD3<Float>(sin(angle) * 90, 0.02, cos(angle) * 90)
            dash.orientation = simd_quatf(angle: angle, axis: SIMD3<Float>(0, 1, 0))
            props.root.addChild(dash)
        }

        // A ring of gates to drive through.
        let gateMaterial = SimpleMaterial(
            color: .init(red: 0.2, green: 0.8, blue: 0.95, alpha: 1), isMetallic: false)
        for index in 0..<8 {
            let angle = Float(index) / 8 * 2 * .pi
            let radius: Float = 55
            let centre = SIMD3<Float>(sin(angle) * radius, 0, cos(angle) * radius)
            props.gateCentres.append(centre)

            let gate = Entity()
            gate.position = centre
            gate.orientation = simd_quatf(angle: angle, axis: SIMD3<Float>(0, 1, 0))

            for side in [-1.0, 1.0] as [Float] {
                let post = ModelEntity(
                    mesh: .generateBox(size: SIMD3<Float>(0.5, 7, 0.5)),
                    materials: [gateMaterial])
                post.position = SIMD3<Float>(side * 5.5, 3.5, 0)
                gate.addChild(post)
            }
            let lintel = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(11.5, 0.5, 0.5)),
                materials: [gateMaterial])
            lintel.position = SIMD3<Float>(0, 7, 0)
            gate.addChild(lintel)
            props.root.addChild(gate)
        }

        // Obstacles to bump into; collisions are what fire the force feedback.
        let obstacleMaterial = SimpleMaterial(
            color: .init(red: 0.95, green: 0.45, blue: 0.25, alpha: 1), isMetallic: false)
        var seed: UInt64 = 0x5EED
        func random() -> Float {
            seed = seed &* 6364136223846793005 &+ 1442695040888963407
            return Float((seed >> 33) & 0xFFFFFF) / Float(0xFFFFFF)
        }
        for _ in 0..<40 {
            let angle = random() * 2 * .pi
            let radius = 14 + random() * 78
            let position = SIMD3<Float>(sin(angle) * radius, 0, cos(angle) * radius)
            // Keep the start line clear.
            if simd_length(position) < 10 { continue }
            props.obstacles.append(position)

            let cone = ModelEntity(
                mesh: .generateCone(height: 2.2, radius: obstacleRadius * 0.7),
                materials: [obstacleMaterial])
            cone.position = SIMD3<Float>(position.x, 1.1, position.z)
            props.root.addChild(cone)
        }

        return props
    }

    /// Distance-based contact test. The car is a circle, as are the obstacles —
    /// enough resolution for a sample, and it cannot tunnel at these speeds
    /// because steps are short relative to the radii.
    static func collision(at position: SIMD3<Float>, in props: Props) -> SIMD3<Float>? {
        for obstacle in props.obstacles {
            let delta = position - obstacle
            let planar = SIMD2<Float>(delta.x, delta.z)
            if simd_length(planar) < carRadius + obstacleRadius {
                return obstacle
            }
        }
        return nil
    }
}