import SwiftUI

/// Wheel / gamepad connection state and the endpoint controls.
struct ConnectionView: View {
    @Bindable var model: DriveViewModel
    @State private var host: String = WheelMCPClient.Configuration.default.host
    @State private var port: String = String(WheelMCPClient.Configuration.default.port)
    @State private var token: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            statusRow("Driving input", model.inputSourceName,
                      ok: model.wheelInputs.connected || model.gamepad.connected)
            statusRow("Force feedback", model.feedback.enabled ? "Engaged" : "Off",
                      ok: model.feedback.enabled)
            statusRow("Anchor", model.anchoredHeading.name, ok: model.anchoredHeading.yaw != nil)

            HStack(spacing: 8) {
                TextField("Host", text: $host).frame(width: 150)
                TextField("Port", text: $port).frame(width: 90)
                TextField("Token (optional)", text: $token).frame(width: 170)
                Button("Apply") { apply() }
            }
            .textFieldStyle(.roundedBorder)

            if let error = model.wheelInputs.lastError {
                Text(error).font(.footnote).foregroundStyle(.orange)
            }
            if let command = model.feedback.lastCommand {
                Text("Last FF: \(command)").font(.footnote).foregroundStyle(.secondary)
            }
            Text(model.anchoredHeading.detail).font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func apply() {
        let configuration = WheelMCPClient.Configuration(
            host: host, port: Int(port) ?? 8765,
            token: token.isEmpty ? nil : token)
        model.wheelInputs.configuration = configuration
        model.feedback.configuration = configuration
    }

    private func statusRow(_ label: String, _ value: String, ok: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(ok ? Color.green : Color.secondary)
                .frame(width: 9, height: 9)
            Text(label)
                .frame(width: 130, alignment: .leading)
                .fixedSize(horizontal: true, vertical: false)
            Text(value)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
    }
}