"""Explicit one-time download; no personal data or credentials are bundled."""
import argparse
from pathlib import Path

MODEL = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("models/qwen3-embedding-0.6b"))
    args = parser.parse_args()
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL, revision=REVISION, local_dir=str(args.output))
    print("Model saved to", args.output.resolve())


if __name__ == "__main__":
    main()
