"""Build pages from data-free templates, escaping JSON for script contexts."""
from pathlib import Path
import re
from .collector import embedded, js_json, replace_data

PROJECT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).with_name("web")
EXAMPLES = Path(__file__).with_name("examples")


def source_page(graph, schema):
    views = {
        "content": (WEB / "content.html").read_text(encoding="utf-8").replace("@@GRAPH@@", js_json(graph)),
        "schema": (WEB / "schema.html").read_text(encoding="utf-8").replace("@@SCHEMA@@", js_json(schema)),
    }
    shell = (WEB / "shell.html").read_text(encoding="utf-8")
    replacements = {"VIEWS": js_json(views), "GRAPH": js_json(graph), "SCHEMA": js_json(schema)}
    return re.sub(r"@@(VIEWS|GRAPH|SCHEMA)@@", lambda match: replacements[match[1]], shell)


def attach_views(payload, templates):
    return {**payload, "views": {"content": replace_data(templates["content"], "const data=", payload["graph"]), "schema": replace_data(templates["schema"], "const schema = ", payload["schema"])}}


def payload_page(source, payload):
    page = replace_data(source, "let views=", payload["views"])
    page = replace_data(page, "let graphData=", payload["graph"])
    return replace_data(page, "schemaData=", payload["schema"])
