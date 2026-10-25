import argparse
import logging
from pathlib import Path
import webbrowser

from .server import close_server, create_server
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Read-only Tana knowledge graph (local use)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Fictional data; no MCP, model or third-party packages needed")
    mode.add_argument("--config", type=Path, help="Local live-mode JSON settings (never commit credentials)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open in your default browser")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1–65535")
    settings = None
    if args.config:
        try:
            settings = Settings.load(args.config)
            if not settings.model_path.is_dir():
                raise ValueError("Model directory missing; run scripts/download_model.py first")
        except (OSError, ValueError, TypeError) as error:
            parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        server = create_server(args.port, settings=settings, demo=args.demo)
    except OSError as error:
        parser.error(f"Cannot listen on 127.0.0.1:{args.port} ({type(error).__name__})")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Tana Local Graph: {url}", flush=True)
    if args.demo:
        print("DEMO: fictional notes and synthetic similarities; not a model result.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        close_server(server)


if __name__ == "__main__":
    main()
