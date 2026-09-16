import RealityKit
import SwiftUI
import simd

/// The immersive driving view: the world lives here and the driving loop runs
/// in `DriveViewModel`, which the RealityView's update closure reads.
struct DriveView: View {
    @Bindable var model: DriveViewModel

    @State private var root = Entity()
    @State private var worldRoot = Entity()
    @State private var carRoot = Entity()
    @State private var cockpit = Entity()

    var body: some View {
        RealityView { content in
            root.addChild(worldRoot)
            root.addChild(carRoot)
            content.add(root)
            buildCar()
            rebuildWorld()
        } update: { _ in
            syncScene()
        }
        // The viewport sits where the driver's head would be, and reads as the
        // cockpit: without it the immersive space is a car seen from outside.
        .overlay(alignment: .bottom) {
            DriveHUD(model: model)
                .padding(40)
        }
    }

    /// The car is assembled from primitives: a low-poly hull plus a cockpit riser
    /// so the first-person view has visible structure around it.
    private func buildCar() {
        let bodyMaterial = SimpleMaterial(
            color: .init(red: 0.22, green: 0.5, blue: 0.95, alpha: 1), isMetallic: false)
        let darkMaterial = SimpleMaterial(
            color: .init(red: 0.12, green: 0.12, blue: 0.14, alpha: 1), isMetallic: false)

        let hull = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(1.8, 0.7, 3.8)),
            materials: [bodyMaterial])
        hull.position = SIMD3<Float>(0, 0.55, 0)
        carRoot.addChild(hull)

        let nose = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(1.5, 0.4, 1.0)),
            materials: [bodyMaterial])
        nose.position = SIMD3<Float>(0, 0.4, 2.2)
        carRoot.addChild(nose)

        let cockpitBlock = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(1.5, 0.6, 1.4)),
            materials: [darkMaterial])
        cockpitBlock.position = SIMD3<Float>(0, 1.1, -0.2)
        carRoot.addChild(cockpitBlock)

        for corner in [SIMD3<Float>(-0.85, 0.32, 1.3), SIMD3<Float>(0.85, 0.32, 1.3),
                       SIMD3<Float>(-0.85, 0.32, -1.3), SIMD3<Float>(0.85, 0.32, -1.3)] {
            let wheel = ModelEntity(
                mesh: .generateCylinder(height: 0.28, radius: 0.34),
                materials: [darkMaterial])
            wheel.position = corner
            wheel.orientation = simd_quatf(angle: .pi / 2, axis: SIMD3<Float>(0, 0, 1))
            carRoot.addChild(wheel)
        }

        // Dashboard bar to read as a cockpit from the driver's eye position.
        let dash = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(1.6, 0.12, 0.25)),
            materials: [darkMaterial])
        dash.position = SIMD3<Float>(0, 1.02, 0.75)
        carRoot.addChild(dash)
    }

    private func rebuildWorld() {
        worldRoot.children.removeAll()
        worldRoot.addChild(model.world.root)
    }

    private func syncScene() {
        carRoot.position = model.state.position
        carRoot.orientation = simd_quatf(angle: model.state.heading, axis: SIMD3<Float>(0, 1, 0))
    }
}

/// Flat HUD drawn over the immersive space.
struct DriveHUD: View {
    @Bindable var model: DriveViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(model.speedKPH) km/h")
                .font(.system(size: 44, weight: .bold, design: .rounded))
                .monospacedDigit()
            Text(model.inputSourceName)
                .font(.callout)
            Text("Car \(model.collisionCount) · \(model.statusMessage)")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(18)
        .glassBackgroundEffect()
    }
}