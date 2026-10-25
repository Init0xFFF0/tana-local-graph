"""A release hygiene check, not a guarantee against every kind of secret."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAD_NAMES = {"config.json", ".env", ".DS_Store", "embeddings.sqlite3", "last-good-snapshot.json"}
SKIP = {".git", ".venv", "__pycache__", "node_modules", "build", "dist"}
# Patterns intentionally split to avoid matching their own definitions.
RULES = [re.compile(r"/Users/" + r"[^/\s]+/"), re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), re.compile(r"gh[pousr]_" + r"[A-Za-z0-9]{30,}"), re.compile(r"github_pat_" + r"[A-Za-z0-9_]{30,}"), re.compile(r"sk-" + r"[A-Za-z0-9_-]{32,}")]


def inspect(root=ROOT):
    issues = []
    for file in root.rglob("*"):
        if not file.is_file() or any(p in SKIP for p in file.relative_to(root).parts):
            continue
        name = file.relative_to(root).as_posix()
        if any(part in {"models", ".local-data", "runtime-cache"} for part in file.relative_to(root).parts):
            issues.append((name, "private runtime directory"))
            continue
        if file.name in BAD_NAMES or file.suffix in {".sqlite3", ".log"}:
            issues.append((name, "private runtime filename"))
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in RULES):
            issues.append((name, "credential or personal path pattern"))
    return issues


if __name__ == "__main__":
    issues = inspect()
    for name, reason in issues:
        print(name + ": " + reason)
    if issues:
        raise SystemExit(1)
    print("PASS public-tree hygiene patterns (not a complete secret audit)")
