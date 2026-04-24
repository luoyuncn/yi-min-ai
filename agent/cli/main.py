"""命令行入口。

这个文件刻意保持很薄：
参数解析和交互循环在这里，
真正的业务处理都交给 `build_app()` 和 `AgentApplication`。
"""

import argparse
from pathlib import Path

from agent.app import build_app


def main() -> None:
    """启动命令行交互循环。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/agent.yaml")
    parser.add_argument("--testing", action="store_true")
    args = parser.parse_args()

    app = build_app(Path(args.config), testing=args.testing)
    # 先打印 ready banner，方便人工验证 CLI 是否正常启动。
    print("Yi Min CLI is ready. Type 'exit' to quit.")

    while True:
        # 用最简单的 REPL 形式维持一期体验：
        # 读一行 -> 处理一轮 -> 打印回复。
        text = input("> ").strip()
        if text in {"exit", "quit"}:
            break
        if not text:
            continue
        try:
            print(app.handle_text(text, session_id="cli:default"))
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()
