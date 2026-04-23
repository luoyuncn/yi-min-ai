"""Web 入口。"""

import argparse
from pathlib import Path

import uvicorn

from agent.web.app import create_web_app


def main() -> None:
    """启动本地 Web Agent 服务。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/agent.yaml")
    parser.add_argument("--testing", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = create_web_app(Path(args.config), testing=args.testing)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
