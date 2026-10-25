"""Read-only MCP Streamable HTTP client, compatible with the 2025-06-18 Tana server."""
import json
import secrets
import threading
import urllib.error
import urllib.request

READ_TOOLS = frozenset({"list_workspaces", "list_tags", "get_children", "read_node", "get_tag_schema"})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


class Tana:
    def __init__(self, settings):
        self.url = settings.mcp_url
        self.headers = dict(settings.headers)
        self.session = None
        self.protocol = None
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(NoRedirect)
        self._initialize()

    def _initialize(self):
        result = self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "tana-local-graph", "version": "0.1.0"}})
        version = result.get("protocolVersion")
        if version not in ("2025-03-26", "2025-06-18", "2025-11-25"):
            raise RuntimeError("Unsupported negotiated MCP version")
        self.protocol = version
        self._request("notifications/initialized", notification=True)

    def _request(self, method, params=None, notification=False):
        request_id = secrets.token_hex(8)
        message = {"jsonrpc": "2.0", "method": method}
        if not notification:
            message["id"] = request_id
        if params is not None:
            message["params"] = params
        headers = {**self.headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), headers=headers)
        try:
            with self.opener.open(request, timeout=45) as response:
                if method == "initialize":
                    self.session = response.headers.get("Mcp-Session-Id")
                if notification:
                    return None
                if response.headers.get_content_type() == "text/event-stream":
                    chunks = []
                    for raw in response:
                        line = raw.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            chunks.append(line[5:].lstrip(" "))
                        elif not line and chunks:
                            obj = json.loads("\n".join(chunks)); chunks = []
                            if obj.get("id") == request_id:
                                return self._result(obj)
                    raise RuntimeError("MCP stream ended before its response")
                obj = json.load(response)
                if obj.get("id") != request_id:
                    raise RuntimeError("MCP response ID mismatch")
                return self._result(obj)
        except urllib.error.HTTPError as error:
            # Do not echo endpoint URLs, request headers or remote response bodies.
            raise RuntimeError(f"MCP HTTP {error.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("MCP endpoint unavailable") from None

    @staticmethod
    def _result(message):
        if "error" in message:
            raise RuntimeError("MCP returned an error")
        return message["result"]

    def call(self, name, args):
        if name not in READ_TOOLS:
            raise ValueError("This graph only allows read-only Tana tools")
        # Serialize tool calls around session recreation; a failed cycle keeps last-good data.
        with self.lock:
            try:
                result = self._request("tools/call", {"name": name, "arguments": args})
            except RuntimeError as error:
                if str(error) != "MCP HTTP 404":
                    raise
                self.session = None; self.protocol = None
                self._initialize()
                result = self._request("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise RuntimeError("Tana tool returned an error")
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result["structuredContent"]
        text = "\n".join(item["text"] for item in result.get("content", []) if item.get("type") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def children(self, node_id):
        result = []; offset = 0
        while True:
            page = self.call("get_children", {"nodeId": node_id, "limit": 100, "offset": offset})
            result.extend(page["children"])
            if not page.get("hasMore"):
                return result
            if not page["children"] or offset > 5000:
                raise RuntimeError("Child list exceeds safety limit; keeping last-good data")
            offset += len(page["children"])
