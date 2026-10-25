import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from tana_graph.mcp_client import Tana
from tana_graph.server import close_server, create_server


class HTTPTests(unittest.TestCase):
    def test_demo_http_and_sse_without_mcp(self):
        with patch("tana_graph.server.Tana", side_effect=AssertionError("Demo must not connect to MCP")):
            server = create_server(port=0, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            payload = json.load(urllib.request.urlopen(url + "/api/data", timeout=3))
            self.assertEqual(payload["graph"]["semantic"]["model"], "synthetic-demo-only")
            self.assertEqual(len(payload["graph"]["nodes"]), 5)
            with urllib.request.urlopen(url + "/api/events", timeout=3) as response:
                packet = b""
                while b"event: snapshot" not in packet or not packet.endswith(b"\n\n"):
                    packet += response.readline()
                state = json.loads(next(line[6:] for line in packet.decode().splitlines() if line.startswith("data: ")))
                self.assertTrue(state["ready"]); self.assertNotIn("graph", state)
            request = urllib.request.Request(url + "/api/data", headers={"Origin": "https://untrusted.example"})
            with self.assertRaises(urllib.error.HTTPError) as result: urllib.request.urlopen(request)
            self.assertEqual(result.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(url + "/config.json")
        finally:
            server.shutdown(); close_server(server); thread.join(timeout=3)

    def test_mcp_initialization_session_sse_and_readonly(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); seen.append(message["method"])
                if message["method"] == "initialize":
                    body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18"}}).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Mcp-Session-Id", "fixture-session"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    if self.headers.get("Mcp-Session-Id") != "fixture-session" or self.headers.get("MCP-Protocol-Version") != "2025-06-18":
                        self.send_error(400); return
                    if message["method"] == "notifications/initialized":
                        self.send_response(202); self.end_headers(); return
                    body = ('event: message\ndata: ' + json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": '[{"id":"fixture-workspace"}]'}]}}) + '\n\n').encode()
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = Tana(SimpleNamespace(mcp_url=f"http://127.0.0.1:{server.server_port}/mcp", headers={}))
            self.assertEqual(client.call("list_workspaces", {}), [{"id": "fixture-workspace"}])
            with self.assertRaises(ValueError): client.call("delete_node", {})
            self.assertEqual(seen, ["initialize", "notifications/initialized", "tools/call"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)
