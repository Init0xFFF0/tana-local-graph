"""Explicit configuration; never reads an editor's configuration or credentials."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    workspace_id: str
    root_ids: tuple[str, ...]
    model_path: Path
    data_dir: Path
    mcp_url: str
    headers: dict[str, str]
    exclude_ids: frozenset[str] = frozenset()
    sync_interval: float = 5.0

    @classmethod
    def load(cls, path: Path):
        path = path.resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        workspace = raw.get("workspace_id", "")
        roots = raw.get("root_ids", [])
        if not isinstance(workspace, str) or not workspace or workspace.startswith("YOUR_"):
            raise ValueError("Configure workspace_id in your local config.json")
        if not isinstance(roots, list) or not roots or not all(isinstance(n, str) and n and not n.startswith("YOUR_") for n in roots):
            raise ValueError("Configure at least one real root_ids entry")
        url = os.environ.get("TANA_MCP_URL", raw.get("mcp_url", ""))
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("MCP URL must be HTTP(S), without embedded credentials, query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Use HTTPS for a non-local MCP endpoint")
        headers = json.loads(os.environ.get("TANA_MCP_HEADERS", "{}"))
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) and "\n" not in k + v and "\r" not in k + v for k, v in headers.items()):
            raise ValueError("TANA_MCP_HEADERS must be a JSON object of header strings")
        reserved = {"host", "content-length", "content-type", "accept", "mcp-session-id", "mcp-protocol-version"}
        if any(k.lower() in reserved for k in headers):
            raise ValueError("Custom headers cannot override transport/session headers")
        token = os.environ.get("TANA_MCP_TOKEN")
        if token:
            if "\n" in token or "\r" in token:
                raise ValueError("Invalid token")
            headers["Authorization"] = "Bearer " + token
        def local_path(value):
            p = Path(value).expanduser()
            return (path.parent / p).resolve() if not p.is_absolute() else p.resolve()
        model = local_path(os.environ.get("QWEN_MODEL_PATH", raw.get("model_path", "models/qwen3-embedding-0.6b")))
        data = local_path(raw.get("data_dir", ".local-data"))
        interval = float(raw.get("sync_interval", 5))
        if not 1 <= interval <= 3600:
            raise ValueError("sync_interval must be between 1 and 3600 seconds")
        excluded = raw.get("exclude_ids", [])
        if not isinstance(excluded, list) or not all(isinstance(n, str) for n in excluded):
            raise ValueError("exclude_ids must be a list of node IDs")
        return cls(workspace, tuple(dict.fromkeys(roots)), model, data, url, headers, frozenset(excluded), interval)
