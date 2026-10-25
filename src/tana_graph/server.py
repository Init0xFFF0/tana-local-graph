"""Loopback-only graph server with last-good snapshots and SSE change metadata."""
import hashlib
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .collector import Graph, embedded, tagged_graph
from .mcp_client import Tana
from .render import EXAMPLES, attach_views, payload_page, source_page
from .snapshot_store import SnapshotStore

APP = "tana-local-graph-v1"
LOG = logging.getLogger(__name__)


def metadata(server):
    snapshot = server.snapshot
    return {"version": server.event_version, "revision": snapshot["revision"] if snapshot else None, "observedAt": snapshot.get("observedAt") if snapshot else None, "ready": snapshot is not None, "syncState": server.sync_state, "cycleSeconds": server.cycle_seconds}


def publish(server, state, payload=None, duration=None):
    with server.updates:
        if payload is not None:
            server.snapshot = payload
            server.ready.set()
        server.sync_state = state
        server.cycle_seconds = duration
        server.event_version += 1
        server.updates.notify_all()


def empty_payload(workspace):
    return {"graph": {"nodes": [], "edges": [], "semantic": {"status": "pending", "ids": [], "values": []}}, "schema": {**{k: [] for k in ("tags", "fields", "extends", "hasField", "instanceOf")}, "metadata": {"workspaceId": workspace}}, "workspace": "Tana", "observedAt": None, "revision": "awaiting-first-sync"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != expected or (origin and origin != "http://" + expected):
            return self.send(403, {"error": "Loopback access only"})
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/health":
            with self.server.updates:
                state = metadata(self.server)
            return self.send(200, {"app": APP, "mode": self.server.mode, **state})
        if path == "/api/events":
            return self.stream_events()
        if path == "/api/data":
            with self.server.updates:
                payload = self.server.snapshot
                state = self.server.sync_state
                version = self.server.event_version
            if payload is None:
                return self.send(503, {"error": "Waiting for the first successful sync"})
            return self.send(200, {**payload, "stale": state != "ready", "syncState": state, "eventVersion": version})
        if path in ("/", "/知识图谱.html"):
            payload = self.server.snapshot or self.server.placeholder
            return self.send(200, payload_page(self.server.source, payload).encode(), "text/html; charset=utf-8")
        return self.send(404, {"error": "Not found"})

    def stream_events(self):
        self.send_response(200)
        for key, value in {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "close"}.items():
            self.send_header(key, value)
        self.end_headers()
        self.connection.settimeout(15)
        last_version = -1
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while not self.server.stop_event.is_set():
                with self.server.updates:
                    self.server.updates.wait_for(lambda: self.server.stop_event.is_set() or self.server.event_version != last_version, timeout=10)
                    if self.server.stop_event.is_set():
                        return
                    state = metadata(self.server)
                if state["version"] != last_version:
                    last_version = state["version"]
                    packet = f"id: {last_version}\nevent: snapshot\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
                else:
                    packet = ": heartbeat\n\n"
                self.wfile.write(packet.encode())
                self.wfile.flush()
        except OSError:
            pass

    def send(self, code, body, kind="application/json; charset=utf-8"):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if kind.startswith("application/json") else body
        self.send_response(code)
        for key, value in {"Content-Type": kind, "Content-Length": str(len(data)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def create_server(port=8765, settings=None, demo=False):
    if not demo and settings is None:
        raise ValueError("Live mode requires explicit settings")
    seed = empty_payload("demo-workspace" if demo else settings.workspace_id)
    source = source_page(seed["graph"], seed["schema"])
    templates = embedded(source, "let views=")[0]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.source = source
    server.templates = templates
    server.mode = "demo" if demo else "live"
    server.snapshot = None
    server.sync_state = "refreshing"
    server.ready = threading.Event()
    server.updates = threading.Condition()
    server.event_version = 0
    server.cycle_seconds = None
    server.stop_event = threading.Event()
    server.placeholder = attach_views(seed, templates)
    server.worker = None
    if demo:
        payload = json.loads((EXAMPLES / "demo.json").read_text(encoding="utf-8"))
        payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        payload["observedAt"] = datetime.now(timezone.utc).isoformat()
        # Synthetic similarities remain explicitly marked; no MCP/model access in demo.
        publish(server, "ready", attach_views(payload, templates), 0)
    else:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        store = SnapshotStore(settings.data_dir / "last-good-snapshot.json", settings.workspace_id, APP)
        cached = store.load()
        if cached:
            cached["graph"] = tagged_graph(cached["graph"])
            publish(server, "refreshing", attach_views(cached, templates))
        def refresh():
            from .similarity import SemanticIndex
            graph = None
            index = None
            failures = 0
            try:
                while not server.stop_event.is_set():
                    start = time.monotonic()
                    publish(server, "refreshing")
                    try:
                        if graph is None:
                            candidate = Graph(source, Tana(settings), settings)
                            if index is None:
                                index = SemanticIndex(settings.data_dir, settings.model_path)
                            candidate.semantic = index
                            graph = candidate
                        payload = graph.collect()
                        if payload["graph"]["semantic"].get("status") != "ready":
                            raise RuntimeError("Local semantic calculation unavailable")
                        try:
                            store.save(payload)
                        except (OSError, ValueError):
                            LOG.exception("Snapshot save failed; serving fresh in-memory data")
                        failures = 0
                        publish(server, "ready", payload, round(time.monotonic() - start, 3))
                    except Exception:
                        failures += 1
                        publish(server, "offline", duration=round(time.monotonic() - start, 3))
                        LOG.exception("Read-only collection cycle failed; keeping last-good data")
                    delay = settings.sync_interval if not failures else min(30, 5 * 2 ** min(failures, 3))
                    server.stop_event.wait(delay)
            finally:
                if index is not None:
                    index.db.close()
        server.worker = threading.Thread(target=refresh, daemon=True)
        server.worker.start()
    return server


def close_server(server):
    server.stop_event.set()
    with server.updates:
        server.updates.notify_all()
    server.server_close()
