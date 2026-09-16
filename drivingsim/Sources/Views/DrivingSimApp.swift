import SwiftUI

@main
struct DrivingSimApp: App {
    @State private var model = DriveViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
        }
        .defaultSize(width: 900, height: 620)

        ImmersiveSpace(id: "drive") {
            DriveView(model: model)
        }
        .immersionStyle(selection: .constant(.full), in: .full)
    }
}