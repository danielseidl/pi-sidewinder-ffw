#!/usr/bin/env python3
"""MCP server exposing the Microsoft SideWinder Force Feedback Wheel over HTTP.

An MCP client speaks JSON-RPC 2.0 to POST /mcp. A GET /sse endpoint is also
served for clients that expect the legacy HTTP+SSE transport, and GET /health
answers for monitoring.

The wheel itself is driven through wheelctl.py, which shares the native PID
protocol implementation. Reading uses hidraw; both need permission on the
device nodes, so run as root or install the udev rule in README.md.

Usage:
    python3 mcp_server.py [--host 0.0.0.0] [--port 8765] [--token TOKEN]

If TOKEN is set (or WHEEL_MCP_TOKEN is in the environment), every request must
carry `Authorization: Bearer TOKEN`. Without a token the server refuses to bind
to a non-loopback address unless --insecure is passed explicitly: force feedback
moves the wheel under its own power, and an open port lets anyone on the network
do that.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wheelctl

SERVER_NAME = "sidewinder-wheel"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "get_status",
        "description": "Report whether the wheel is present, plus device and "
                       "server facts. Start here to confirm the wheel is "
                       "connected and powered.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_position",
        "description": "Read the current steering position once. Returns the raw "
                       "value (-512..511) and a normalized value (-1.0 full left "
                       "to +1.0 full right), plus the aux axes and buttons. The "
                       "wheel only sends a report when something changes, so turn "
                       "it while this is called or increase timeout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {"type": "number", "description": "Seconds to wait for a report. Default 2.", "default": 2},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "watch_position",
        "description": "Sample the steering position over a short window and "
                       "return the series. Use this to observe movement without "
                       "holding a stream open.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "How long to sample. Default 3.", "default": 3},
                "max_samples": {"type": "integer", "description": "Stop after this many samples. Default 200.", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "set_center",
        "description": "Engage a centring spring, so the wheel resists being turned "
                       "away from centre and returns to it. This is the default "
                       "resting mode. Pass seconds to auto-release, or omit it to "
                       "leave the spring engaged until stop() is called.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Auto-release after this long. Omit to keep it engaged."},
                "strength": {"type": "integer", "description": "Spring coefficient, 0-127. Default 63.", "default": 63},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "set_damper",
        "description": "Engage a damper, so the wheel resists being turned in "
                       "proportion to how fast it is moved. Pass seconds to "
                       "auto-release, or omit to keep it engaged until stop().",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Auto-release after this long. Omit to keep it engaged."},
                "strength": {"type": "integer", "description": "Damper coefficient, 0-127. Default 63.", "default": 63},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "play_constant",
        "description": "Apply a steady force for a fixed time. Positive pushes "
                       "right, negative pushes left.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Force, -255 (full left) to +255 (full right)."},
                "seconds": {"type": "number", "description": "Duration. Default 2.", "default": 2},
            },
            "required": ["level"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sweep",
        "description": "Sweep a constant force from full-right to full-left and "
                       "back through the range, pausing at each step. Useful for "
                       "confirming the full force range works.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "step_seconds": {"type": "number", "description": "Pause per step. Default 1.2.", "default": 1.2},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "stop",
        "description": "Stop all effects and disable the actuators, releasing the "
                       "wheel to turn freely.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class Wheel:
    """Serialises access to the device.

    Effects run in a background thread so a long play_constant does not block
    the HTTP request loop, but only one effect may be active at a time: two
    concurrent writers to the same effect block produce nonsense.
    """

    def __init__(self, device=None):
        self.device = device
        self.lock = threading.Lock()
        self.worker = None
        self.stop_flag = threading.Event()

    def _fd(self):
        return wheelctl.open_wheel(self.device)

    def status(self):
        present, detail = wheelctl.probe(self.device)
        return {
            "present": present,
            "device": detail.get("path"),
            "name": detail.get("name"),
            "block": wheelctl.BLOCK,
            "detail": detail.get("error"),
            "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def read_position(self, timeout=2.0):
        fd = self._fd()
        try:
            return wheelctl.read_sample(fd, timeout=timeout)
        finally:
            os.close(fd)

    def watch(self, seconds, max_samples):
        fd = self._fd()
        try:
            return wheelctl.collect(fd, seconds=seconds, max_samples=max_samples)
        finally:
            os.close(fd)

    def _cancel_worker(self):
        self.stop_flag.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=5)
        self.worker = None
        self.stop_flag = threading.Event()

    def run_effect(self, fn, seconds):
        """Run fn(fd, stop_flag) in a worker thread."""
        with self.lock:
            self._cancel_worker()
            fd = self._fd()
            flag = self.stop_flag

            def body():
                try:
                    fn(fd, flag, seconds)
                finally:
                    try:
                        wheelctl.stop_all(fd)
                    except OSError:
                        pass
                    os.close(fd)

            self.worker = threading.Thread(target=body, daemon=True)
            self.worker.start()

    def set_center(self, seconds, strength):
        self.run_effect(
            lambda fd, flag, secs: wheelctl.run_condition(
                fd, wheelctl.ET_SPRING, secs, flag, coeff=strength),
            seconds)

    def set_damper(self, seconds, strength):
        self.run_effect(
            lambda fd, flag, secs: wheelctl.run_condition(
                fd, wheelctl.ET_DAMPER, secs, flag, coeff=strength),
            seconds)

    def play_constant(self, level, seconds):
        self.run_effect(
            lambda fd, flag, secs: wheelctl.run_constant(fd, level, secs, flag),
            seconds)

    def sweep(self, step_seconds):
        self.run_effect(
            lambda fd, flag, secs: wheelctl.run_sweep(fd, secs, flag, step_seconds=step_seconds),
            0)

    def stop(self):
        with self.lock:
            self._cancel_worker()
            fd = self._fd()
            try:
                wheelctl.stop_all(fd)
            finally:
                os.close(fd)


WHEEL = Wheel()


def call_tool(name, arguments):
    """Dispatch a tool call. Returns (text, is_error)."""
    try:
        if name == "get_status":
            return _json(WHEEL.status()), False

        if name == "read_position":
            timeout = float(arguments.get("timeout", 2))
            sample = WHEEL.read_position(timeout)
            if sample is None:
                return ("no steering report arrived within %gs. The wheel only "
                        "sends a report when something changes, so turn it while "
                        "calling, or raise timeout. If turning does nothing, check "
                        "the wheel's power adapter." % timeout), True
            return _json(_decorate(sample)), False

        if name == "watch_position":
            samples = WHEEL.watch(
                float(arguments.get("seconds", 3)),
                int(arguments.get("max_samples", 200)))
            if not samples:
                return ("no steering reports arrived; is the wheel powered?",
                        True)
            levels = [s["normalized"] for s in samples]
            return _json({
                "samples": [_decorate(s) for s in samples],
                "count": len(samples),
                "min": min(levels),
                "max": max(levels),
                "span": round(max(levels) - min(levels), 3),
            }), False

        if name == "set_center":
            WHEEL.set_center(_opt_float(arguments.get("seconds")),
                             int(arguments.get("strength", 63)))
            return _ack("centre spring", arguments.get("seconds")), False

        if name == "set_damper":
            WHEEL.set_damper(_opt_float(arguments.get("seconds")),
                             int(arguments.get("strength", 63)))
            return _ack("damper", arguments.get("seconds")), False

        if name == "play_constant":
            if "level" not in arguments:
                return "play_constant requires 'level'", True
            level = int(arguments["level"])
            if not -255 <= level <= 255:
                return "level must be between -255 and 255", True
            seconds = float(arguments.get("seconds", 2))
            WHEEL.play_constant(level, seconds)
            return "playing constant force %+d for %gs" % (level, seconds), False

        if name == "sweep":
            WHEEL.sweep(float(arguments.get("step_seconds", 1.2)))
            return "sweeping constant force across the full range", False

        if name == "stop":
            WHEEL.stop()
            return "all effects stopped, actuators disabled", False

        return "unknown tool: %s" % name, True
    except OSError as exc:
        return "device error: %s" % exc, True
    except (TypeError, ValueError) as exc:
        return "bad arguments: %s" % exc, True


def _opt_float(value):
    return None if value is None else float(value)


def _decorate(sample):
    return {
        "raw": sample["raw"],
        "normalized": sample["normalized"],
        "y": sample["y"],
        "rz": sample["rz"],
        "buttons": sample["buttons"],
    }


def _ack(label, seconds):
    if seconds:
        return "%s engaged for %gs" % (label, seconds)
    return "%s engaged until stop() is called" % label


def _json(obj):
    return json.dumps(obj, indent=2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)

    def log_message(self, fmt, *args):
        sys.stderr.write("[mcp] %s - %s\n" % (self.address_string(), fmt % args))

    # -- auth ---------------------------------------------------------------

    def _authorised(self):
        token = self.server.token
        if not token:
            return True
        supplied = self.headers.get("Authorization", "")
        return supplied == "Bearer " + token

    def _deny(self):
        self._respond(401, {"error": "unauthorised"},
                      extra_headers={"WWW-Authenticate": "Bearer"})

    # -- helpers ------------------------------------------------------------

    def _respond(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return None

    # -- routes -------------------------------------------------------------

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "server": SERVER_NAME,
                                "version": SERVER_VERSION})
            return
        if self.path.startswith("/sse"):
            self._sse()
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            self._respond(404, {"error": "not found"})
            return
        if not self._authorised():
            self._deny()
            return
        request = self._read_body()
        if request is None:
            self._respond(400, _rpc_error(None, -32700, "parse error"))
            return
        response = handle_rpc(request)
        if response is None:
            # Notification: no reply.
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._respond(200, response)

    def _sse(self):
        """Minimal HTTP+SSE transport for clients that expect it."""
        if not self._authorised():
            self._deny()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        endpoint = "/mcp"
        try:
            self.wfile.write(("event: endpoint\ndata: %s\n\n" % endpoint).encode())
            self.wfile.flush()
            while True:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(15)
        except (BrokenPipeError, ConnectionResetError):
            return


def handle_rpc(request):
    """Handle one JSON-RPC message. Returns the response dict, or None."""
    if isinstance(request, list):
        responses = [r for r in (handle_rpc_one(m) for m in request) if r]
        return responses or None
    return handle_rpc_one(request)


def handle_rpc_one(msg):
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "invalid request")
    method = msg.get("method")
    msg_id = msg.get("id")

    if method is None:
        return None if msg_id is None else _rpc_error(msg_id, -32600, "invalid request")

    if msg_id is None:
        return None  # notification

    if method == "initialize":
        params = msg.get("params") or {}
        return _rpc_result(msg_id, {
            "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return _rpc_result(msg_id, {})

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object")
        text, is_error = call_tool(name, arguments)
        return _rpc_result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    return _rpc_error(msg_id, -32601, "method not found: %s" % method)


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address. Default 127.0.0.1 (loopback only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("WHEEL_MCP_TOKEN"),
                        help="require this bearer token. Also read from WHEEL_MCP_TOKEN")
    parser.add_argument("--device", default=None, help="hidraw node (default: autodetect)")
    parser.add_argument("--insecure", action="store_true",
                        help="allow binding to a non-loopback address with no token")
    args = parser.parse_args()

    loopback = args.host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not args.token and not args.insecure:
        sys.exit(
            "refusing to bind to %s with no token: anyone reaching this port "
            "could move the wheel. Set --token (or WHEEL_MCP_TOKEN), or pass "
            "--insecure if the network is trusted." % args.host)

    WHEEL.device = args.device

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.token = args.token
    present, detail = wheelctl.probe(args.device)
    where = detail.get("path") or detail.get("error")

    print("%s %s listening on http://%s:%d/mcp" %
          (SERVER_NAME, SERVER_VERSION, args.host, args.port), file=sys.stderr)
    print("wheel: %s (%s)" % ("present" if present else "NOT FOUND", where), file=sys.stderr)
    print("auth: %s" % ("bearer token required" if args.token else "none"), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()