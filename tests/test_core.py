import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tana_graph.collector import Graph, embedded, outline_node_references, reference_owners
from tana_graph.render import EXAMPLES, source_page
from tana_graph.settings import Settings
from tana_graph.snapshot_store import SnapshotStore


def demo():
    return json.loads((EXAMPLES / "demo.json").read_text(encoding="utf-8"))


class CoreTests(unittest.TestCase):
    def test_template_roundtrip_and_script_escaping(self):
        payload = demo()
        payload["graph"]["nodes"][0]["name"] = '</script><script>alert(1)</script>@@SCHEMA@@'
        source = source_page(payload["graph"], payload["schema"])
        self.assertEqual(embedded(source, "let graphData=")[0], payload["graph"])
        self.assertNotIn('</script><script>alert(1)', source)
        views = embedded(source, "let views=")[0]
        self.assertEqual(embedded(views["content"], "const data=")[0], payload["graph"])

    def test_reference_exclusions_and_cycles(self):
        ids = {"a", "b", "target"}
        text = '- A <!-- node-id: a -->\n  - **Topic**: [Target](tana:target)\n    - [Target](tana:target)\n  - [B](tana:b)\n```\n  - [Target](tana:target)\n```\n  - [A](tana:a)\n'
        self.assertEqual(outline_node_references("a", text, ids), {("a", "b")})
        self.assertEqual(reference_owners("u", {"u": {"v", "a"}, "v": {"u", "b"}}, ids), {"a", "b"})
        self.assertEqual(reference_owners("b", {"b": {"a"}}, ids), {"b"})

    def test_shared_child_collector(self):
        payload = demo()
        source = source_page(payload["graph"], payload["schema"])
        def node(nid, name, tagged=True, count=0):
            return {"id": nid, "name": name, "docType": "content", "tagIds": ["fixture-tag"] if tagged else [], "childCount": count}
        a = node("a", "A", count=1); b = node("b", "B", count=1); target = node("target", "Target")
        shared = node("shared", '<span data-inlineref-node="target">Target</span>', False)
        class API:
            def children(self, nid):
                return {"fixture-root": [a, b, target], "a": [shared, shared], "b": [shared]}.get(nid, [])
            def call(self, name, args):
                if name == "list_workspaces": return [{"id": "fixture-workspace", "name": "Fixture"}]
                if name == "list_tags": return [{"id": "fixture-tag", "name": "Note"}]
                if name == "get_tag_schema": return '# Tag definition: Note (id:fixture-tag)'
                if name == "read_node": return '- ' + args["nodeId"] + '\n'
                raise AssertionError(name)
        class Layout:
            def build(self, nodes, *_): return {n["id"]: {"x": n["x"], "y": n["y"]} for n in nodes}
        class Semantic:
            def build(self, nodes): return {"status": "ready", "ids": [n["id"] for n in nodes], "values": [[1 if a is b else .5 for b in nodes] for a in nodes]}
        module = types.ModuleType("tana_graph.semantic_layout"); module.SemanticLayout = Layout
        settings = SimpleNamespace(workspace_id="fixture-workspace", root_ids=["fixture-root"], exclude_ids=set())
        with patch.dict("sys.modules", {"tana_graph.semantic_layout": module}), patch("tana_graph.collector.safe_enrich_graph", lambda g: g):
            graph = Graph(source, API(), settings); graph.semantic = Semantic(); collected = graph.collect()
        refs = [(e["source"], e["target"]) for e in collected["graph"]["edges"] if e["type"] == "ref"]
        self.assertEqual(refs, [("a", "target"), ("b", "target")])

    def test_snapshot_preserves_previous_success(self):
        payload = demo(); payload.update(revision="fixture-v1", observedAt="2026-01-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(Path(tmp) / "snapshot.json", "demo-workspace", "fixture-app")
            store.save(payload)
            for modify in [lambda g: g["semantic"]["values"].pop(), lambda g: g["edges"].append({"source": "missing", "target": "other"}), lambda g: g["semantic"]["values"][0].__setitem__(0, float("nan"))]:
                broken = copy.deepcopy(payload); modify(broken["graph"])
                with self.assertRaises(ValueError): store.save(broken)
                self.assertEqual(store.load()["revision"], "fixture-v1")

    def test_settings_reject_placeholders_and_remote_plain_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            data = {"workspace_id": "YOUR_WORKSPACE_ID", "root_ids": ["fixture-root"], "mcp_url": "http://127.0.0.1:8262/mcp"}
            config.write_text(json.dumps(data))
            with self.assertRaises(ValueError): Settings.load(config)
            data.update(workspace_id="fixture-workspace", mcp_url="http://example.com/mcp")
            config.write_text(json.dumps(data))
            with self.assertRaises(ValueError): Settings.load(config)
