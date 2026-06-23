from __future__ import annotations
import argparse
import socket
import subprocess
from typing import Sequence

import uvicorn


def _tailscale_host() -> str | None:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2
        )
        ip = out.stdout.strip().splitlines()
        return ip[0] if ip else None
    except Exception:
        return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m croesus.web")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db-path", default=None)
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    ts = _tailscale_host() or socket.gethostname()
    print(f"Croesus dashboard → http://{ts}:{args.port}  (local: http://127.0.0.1:{args.port})")
    from croesus.web import create_app

    uvicorn.run(create_app(args.db_path), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
