"""Local-only launcher for the optional browser client."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Local literature research browser client")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--monitor", action="store_true", help="Poll saved schedules while this server runs")
    parser.add_argument("--poll-seconds", type=float, default=60)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.poll_seconds < 1:
        parser.error("port must be 1–65535 and poll-seconds at least 1")
    import uvicorn
    from research_engine.web.app import create_app

    uvicorn.run(create_app(data_directory=args.data_dir,
                          monitor=args.monitor, poll_seconds=args.poll_seconds),
                host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
