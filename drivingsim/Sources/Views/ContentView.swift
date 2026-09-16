import SwiftUI

/// Entry window: connection state, then a button into the immersive drive.
struct ContentView: View {
    @Bindable var model: DriveViewModel
    @Environment(\.openImmersiveSpace) private var openImmersiveSpace
    @Environment(\.dismissImmersiveSpace) private var dismissImmersiveSpace
    @State private var immersive = false

    /// Opt-in launch straight into the immersive space, for automated runs where
    /// there is no hand input to press the button with.
    private var autoDrive: Bool {
        ProcessInfo.processInfo.environment["DRIVINGSIM_AUTODRIVE"] == "1"
    }

    /// A synthetic driver used only in automated runs, so the vehicle loop and
    /// HUD can be exercised without a wheel, a gamepad or hands.
    private var scriptedDrive: Bool {
        ProcessInfo.processInfo.environment["DRIVINGSIM_SCRIPTED"] == "1"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("SideWinder Driving Sim")
                .font(.largeTitle.bold())

            ConnectionView(model: model)

            Divider()

            HStack(spacing: 14) {
                Button(immersive ? "Exit Drive" : "Start Driving") {
                    Task { await toggleImmersive() }
                }
                .buttonStyle(.borderedProminent)

                Button("Reset Car") { model.reset() }
            }

            Text(model.statusMessage)
                .font(.callout)
                .foregroundStyle(.secondary)

            Text("Buttons: A resets the car, B toggles the heading source, either shoulder is the handbrake.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(28)
        .frame(minWidth: 660, alignment: .leading)
        .task {
            if autoDrive && !immersive {
                await toggleImmersive()
            }
            if scriptedDrive {
                model.runScriptedDriver()
            }
        }
    }

    private func toggleImmersive() async {
        if immersive {
            await dismissImmersiveSpace()
            immersive = false
            model.stop()
        } else {
            model.start()
            switch await openImmersiveSpace(id: "drive") {
            case .opened: immersive = true
            default: model.stop()
            }
        }
    }
}