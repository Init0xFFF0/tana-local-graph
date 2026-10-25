"""Create a source ZIP from an explicit allowlist after release-hygiene checks."""
import argparse
import hashlib
from pathlib import Path
import zipfile

from check_public_tree import inspect

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {"README.md", "pyproject.toml", ".gitignore", ".env.example", "config.example.json", "requirements-tested.txt", "run.sh", "启动演示.command", "LICENSE-NOTICE.md"}
DIRS = {"src", "tests", "scripts", "docs", ".github"}
SKIP = {"__pycache__", "node_modules", "build", "dist"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "tana-local-graph-v0.1.0.zip")
    args = parser.parse_args()
    issues = inspect(ROOT)
    if issues:
        raise SystemExit("Release tree failed hygiene checks; run scripts/check_public_tree.py")
    files = []
    for file in ROOT.rglob("*"):
        relative = file.relative_to(ROOT)
        if not file.is_file() or any(part in SKIP or part.endswith(".egg-info") for part in relative.parts):
            continue
        if file.suffix in {".pyc", ".pyo"}:
            continue
        if (len(relative.parts) == 1 and file.name in TOP_FILES) or relative.parts[0] in DIRS:
            files.append(file)
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit("Write release archives outside the source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = "\n".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(ROOT).as_posix()}" for file in sorted(files)) + "\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, ROOT.name + "/" + file.relative_to(ROOT).as_posix())
        archive.writestr(ROOT.name + "/MANIFEST.sha256", manifest)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
    print(f"Created {output.name}: {len(files)} files plus manifest, {output.stat().st_size} bytes")
    print("SHA256", digest)


if __name__ == "__main__":
    main()
