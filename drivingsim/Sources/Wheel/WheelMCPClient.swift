import Foundation
import Logging

/// Client for the wheel's MCP server (see the `mcp_server.py` in this repo).
///
/// Deliberately hand-rolled rather than shared with the Innoactive Spatial app:
/// that app embeds an MCP *server* and has no client, and this sample must not
/// depend on that codebase at all.
actor WheelMCPClient {
    struct Configuration: Sendable, Equatable {
        var host: String
        var port: Int
        var token: String?

        static let `default` = Configuration(host: "10.14.28.37", port: 8765, token: nil)

        var url: URL? {
            URL(string: "http://\(host):\(port)/mcp")
        }

        var displayName: String { "\(host):\(port)" }
    }

    private let logger = Logger(label: "drivingsim.WheelMCPClient")
    private let session: URLSession
    private var nextID = 1

    init() {
        let config = URLSessionConfiguration.ephemeral
        // The wheel only reports on change, so a read can legitimately block
        // until a control moves. These are per-request deadlines, not
        // correctness gates: no decision depends on a timer firing.
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 30
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }

    struct ToolResult: Sendable {
        var text: String
        var isError: Bool
    }

    enum ClientError: Error, LocalizedError {
        case notConfigured
        case transport(String)
        case malformedResponse
        case server(String)

        var errorDescription: String? {
            switch self {
            case .notConfigured: "No MCP endpoint configured"
            case .transport(let detail): "Transport error: \(detail)"
            case .malformedResponse: "Malformed response from the wheel server"
            case .server(let detail): "Wheel server error: \(detail)"
            }
        }
    }

    func call(_ tool: String, arguments: [String: any Sendable] = [:],
              configuration: Configuration) async throws -> ToolResult {
        guard let url = configuration.url else { throw ClientError.notConfigured }

        let id = nextID
        nextID += 1
        let body: [String: Any] = [
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": ["name": tool, "arguments": arguments],
        ]

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = configuration.token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw ClientError.transport(error.localizedDescription)
        }

        if let http = response as? HTTPURLResponse, http.statusCode == 401 {
            throw ClientError.server("unauthorised — check the token")
        }

        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ClientError.malformedResponse
        }

        if let error = root["error"] as? [String: Any] {
            let message = error["message"] as? String ?? "unknown"
            throw ClientError.server(message)
        }

        guard let result = root["result"] as? [String: Any],
              let content = result["content"] as? [[String: Any]],
              let first = content.first,
              let text = first["text"] as? String else {
            throw ClientError.malformedResponse
        }

        let isError = result["isError"] as? Bool ?? false
        if isError {
            logger.debug("Wheel tool \(tool) reported: \(text)")
        }
        return ToolResult(text: text, isError: isError)
    }

    func listTools(configuration: Configuration) async throws -> [String] {
        guard let url = configuration.url else { throw ClientError.notConfigured }
        let body: [String: Any] = ["jsonrpc": "2.0", "id": nextID, "method": "tools/list"]
        nextID += 1

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = configuration.token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: request)
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let result = root["result"] as? [String: Any],
              let tools = result["tools"] as? [[String: Any]] else {
            throw ClientError.malformedResponse
        }
        return tools.compactMap { $0["name"] as? String }
    }
}