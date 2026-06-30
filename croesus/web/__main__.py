from __future__ import annotations
import argparse
import os
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
    p.add_argument(
        "--schedule", metavar="HH:MM", default=None,
        help="매일 지정한 로컬 시각에 데이터를 자동 갱신(예: --schedule 18:00). "
             "장 마감 후 시각을 권장합니다.",
    )
    p.add_argument(
        "--reload", action="store_true",
        help="코드 파일 변경을 감지해 서버를 자동 재시작(개발용). "
             "git pull/merge로 파일이 갱신되면 자동 반영됩니다. 운영에서는 끄세요.",
    )
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    schedule_at = None
    if args.schedule:
        from croesus.web.scheduler import parse_run_at
        schedule_at = parse_run_at(args.schedule)

    ts = _tailscale_host() or socket.gethostname()
    print(f"Croesus dashboard → http://{ts}:{args.port}  (local: http://127.0.0.1:{args.port})")
    if schedule_at is not None:
        print(f"자동 데이터 갱신: 매일 {schedule_at.strftime('%H:%M')}")

    if args.reload:
        # reload 모드에서는 uvicorn이 자식 프로세스에서 앱을 import string으로 다시
        # 만들기 때문에, 설정을 환경변수로 넘겨 app_factory가 읽게 한다.
        if args.db_path:
            os.environ["CROESUS_DB_PATH"] = str(args.db_path)
        if args.schedule:
            os.environ["CROESUS_SCHEDULE_AT"] = str(args.schedule)
        print("코드 자동 재시작(reload) 활성화 — 파일이 바뀌면 서버가 다시 뜹니다.")
        uvicorn.run(
            "croesus.web:app_factory",
            factory=True,
            reload=True,
            host=args.host,
            port=args.port,
        )
        return

    from croesus.web import create_app

    uvicorn.run(create_app(args.db_path, schedule_at=schedule_at), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
