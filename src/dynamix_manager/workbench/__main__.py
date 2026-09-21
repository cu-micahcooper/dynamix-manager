"""Start a loopback-only personal ticket workbench."""

import argparse

import uvicorn
from dotenv import load_dotenv

from .app import create_app


def main():
    parser = argparse.ArgumentParser(
        description="Personal TeamDynamix ticket workbench"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    load_dotenv(override=False)
    uvicorn.run(
        create_app(data_dir=args.data_dir),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
