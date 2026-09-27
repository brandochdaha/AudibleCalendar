#!/usr/bin/env python3
"""Run AudibleCalendar from the repository's native virtual environment."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="listen address; defaults to AUDIBLECALENDAR_BIND or 127.0.0.1")
    parser.add_argument("--port", type=int, help="listen port; defaults to AUDIBLECALENDAR_PORT or 8000")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    env_file = root / ".env"
    if not env_file.is_file():
        raise SystemExit("No .env file found. Run the platform install script first.")

    from dotenv import dotenv_values
    import uvicorn

    values = dotenv_values(env_file)
    host = args.host or os.environ.get("AUDIBLECALENDAR_BIND") or values.get("AUDIBLECALENDAR_BIND") or "127.0.0.1"
    port_value = (
        args.port
        if args.port is not None
        else os.environ.get("AUDIBLECALENDAR_PORT") or values.get("AUDIBLECALENDAR_PORT") or "8000"
    )
    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("AUDIBLECALENDAR_PORT must be a number between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("AUDIBLECALENDAR_PORT must be a number between 1 and 65535")

    print(f"AudibleCalendar is starting at http://{host}:{port}")
    print("Press Ctrl+C to stop the server.")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        env_file=str(env_file),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS") or values.get("FORWARDED_ALLOW_IPS") or "127.0.0.1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
