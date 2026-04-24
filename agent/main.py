"""Yi Min AI Agent 统一启动入口

支持多种模式：
- cli: 命令行交互
- web: Web UI (CopilotKit)
- gateway: 飞书 + Heartbeat + Cron（生产推荐）
- all: Web + Gateway 同时启动
"""

import asyncio
import logging
import os
from pathlib import Path

import click

from agent.observability.logging import setup_logging
from agent.runtime_paths import resolve_base_workspace

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["cli", "web", "gateway", "all"]),
    default="gateway",
    help="启动模式（默认: gateway）",
)
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/agent.yaml",
    help="配置文件路径",
)
@click.option(
    "--testing/--no-testing",
    default=False,
    help="测试模式（不需要 API Key）",
)
@click.option(
    "--enable-feishu/--no-feishu",
    default=True,
    help="是否启用飞书通道（默认启用）",
)
@click.option(
    "--enable-heartbeat/--no-heartbeat",
    default=True,
    help="是否启用 Heartbeat（默认启用）",
)
@click.option(
    "--heartbeat-interval",
    type=int,
    default=30,
    help="Heartbeat 间隔（分钟，默认 30）",
)
@click.option(
    "--enable-cron/--no-cron",
    default=True,
    help="是否启用 Cron（默认启用）",
)
@click.option(
    "--web-port",
    type=int,
    default=8000,
    help="Web UI 端口（默认 8000）",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    default="INFO",
    help="日志级别",
)
def main(
    mode: str,
    config: str,
    testing: bool,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    web_port: int,
    log_level: str,
):
    """Yi Min AI Agent 统一启动入口

    \b
    使用示例：
        # 生产模式（飞书 + Heartbeat + Cron）
        uv run python -m agent.main

        # CLI 测试模式
        uv run python -m agent.main --mode cli --testing

        # Web UI 模式
        uv run python -m agent.main --mode web

        # 同时启动 Web + Gateway
        uv run python -m agent.main --mode all

        # 自定义配置
        uv run python -m agent.main --heartbeat-interval 10 --log-level DEBUG

    \b
    环境变量（生产模式需要）：
        FEISHU_APP_ID      - 飞书应用 ID
        FEISHU_APP_SECRET  - 飞书应用密钥
        OPENAI_API_KEY     - OpenAI API Key（或 ANTHROPIC_API_KEY）
    """

    # 初始化日志
    workspace_dir = resolve_base_workspace(Path(config))
    workspace_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = workspace_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(
        log_file=logs_dir / "agent.log",
        level=log_level,
    )

    # 打印启动信息
    _print_banner(mode, testing)

    # 根据模式启动
    if mode == "cli":
        _run_cli(config, testing)
    elif mode == "web":
        _run_web(config, testing, web_port)
    elif mode == "gateway":
        asyncio.run(_run_gateway(
            config_path=Path(config),
            testing=testing,
            enable_feishu=enable_feishu,
            enable_heartbeat=enable_heartbeat,
            heartbeat_interval=heartbeat_interval,
            enable_cron=enable_cron,
        ))
    elif mode == "all":
        asyncio.run(_run_all(
            config_path=Path(config),
            testing=testing,
            enable_feishu=enable_feishu,
            enable_heartbeat=enable_heartbeat,
            heartbeat_interval=heartbeat_interval,
            enable_cron=enable_cron,
            web_port=web_port,
        ))


def _print_banner(mode: str, testing: bool):
    """打印启动 Banner"""
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║           Yi Min AI Agent - v1.1                         ║
║                                                          ║
║   Mode: {mode.upper():<20} Testing: {str(testing):<15}║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
"""
    print(banner)


def _run_cli(config: str, testing: bool):
    """运行 CLI 模式"""
    from agent.cli.main import main as cli_main
    import sys

    sys.argv = ["cli", "--config", config]
    if testing:
        sys.argv.append("--testing")

    cli_main()


def _run_web(config: str, testing: bool, port: int):
    """运行 Web 模式"""
    from agent.web.main import main as web_main
    import sys

    sys.argv = ["web", "--config", config, "--port", str(port)]
    if testing:
        sys.argv.append("--testing")

    web_main()


async def _run_gateway(
    config_path: Path,
    testing: bool,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
):
    """运行 Gateway 模式"""
    from agent.app import build_channel_apps_async
    from agent.gateway.server import GatewayServer
    from agent.scheduler import HeartbeatScheduler, CronScheduler

    # 1. 构建 Agent 应用
    logger.info("正在加载 Agent 应用...")
    settings, apps = await build_channel_apps_async(config_path, testing=testing)
    default_app = apps.get("default") or next(iter(apps.values()))
    workspace_dir = settings.agent.workspace_dir
    logger.info("✓ Agent 应用加载完成")

    # 2. 初始化 Gateway
    gateway = GatewayServer(default_app)
    for runtime_id, app in apps.items():
        gateway.register_runtime_app(runtime_id, app)

    multi_runtime_mode = bool(settings.channels and settings.channels.instances)

    # 3. 注册飞书通道
    if enable_feishu and not testing:
        if multi_runtime_mode:
            for instance in settings.channels.instances:
                if instance.channel_type != "feishu":
                    logger.warning("暂不支持的渠道类型: %s", instance.channel_type)
                    continue

                feishu_app_id = os.environ.get(instance.app_id_env or "")
                feishu_app_secret = os.environ.get(instance.app_secret_env or "")
                if not feishu_app_id or not feishu_app_secret:
                    logger.warning(
                        "渠道实例 %s 缺少飞书凭证环境变量：%s / %s",
                        instance.name,
                        instance.app_id_env,
                        instance.app_secret_env,
                    )
                    continue

                logger.info("正在连接飞书实例 %s（APP_ID: %s...）", instance.name, feishu_app_id[:10])
                try:
                    await gateway.register_feishu(
                        feishu_app_id,
                        feishu_app_secret,
                        adapter_id=instance.name,
                    )
                    logger.info("✓ 飞书实例已连接: %s", instance.name)
                except Exception as e:
                    logger.error("✗ 飞书实例 %s 连接失败: %s", instance.name, e)
        else:
            feishu_app_id = os.environ.get("FEISHU_APP_ID")
            feishu_app_secret = os.environ.get("FEISHU_APP_SECRET")

            if not feishu_app_id or not feishu_app_secret:
                logger.warning(
                    "飞书凭证未设置，跳过飞书通道。\n"
                    "  提示：设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量"
                )
            else:
                logger.info(f"正在连接飞书（APP_ID: {feishu_app_id[:10]}...）")
                try:
                    await gateway.register_feishu(feishu_app_id, feishu_app_secret)
                    logger.info("✓ 飞书通道已连接")
                except Exception as e:
                    logger.error(f"✗ 飞书通道连接失败: {e}")
                    logger.warning("将继续运行，但飞书通道不可用")

    if multi_runtime_mode and (enable_heartbeat or enable_cron):
        logger.warning("多 runtime 模式下暂未支持 Heartbeat/Cron 扇出，已自动禁用")
        enable_heartbeat = False
        enable_cron = False

    # 4. 启动 Heartbeat
    heartbeat_scheduler = None
    if enable_heartbeat:
        logger.info(f"启动 Heartbeat 调度器（间隔: {heartbeat_interval} 分钟）")
        heartbeat_scheduler = HeartbeatScheduler(
            workspace_dir=workspace_dir,
            agent_core=default_app.core,
            gateway=gateway,
            interval_minutes=heartbeat_interval,
        )
        await heartbeat_scheduler.start()
        logger.info("✓ Heartbeat 调度器已启动")

    # 5. 启动 Cron
    cron_scheduler = None
    if enable_cron:
        logger.info("启动 Cron 调度器")
        cron_scheduler = CronScheduler(
            config_path=workspace_dir / "CRON.yaml",
            workspace_dir=workspace_dir,
            agent_core=default_app.core,
            gateway=gateway,
        )
        await cron_scheduler.start()
        logger.info("✓ Cron 调度器已启动")

    # 6. 启动 Gateway 主循环
    logger.info("=" * 60)
    logger.info("Gateway 服务器运行中...")
    logger.info("按 Ctrl+C 停止服务器")
    logger.info("=" * 60)

    try:
        await gateway.start()
    except KeyboardInterrupt:
        logger.info("\n收到停止信号，正在关闭...")
    finally:
        # 清理资源
        if heartbeat_scheduler:
            await heartbeat_scheduler.stop()
        if cron_scheduler:
            await cron_scheduler.stop()
        await gateway.stop()
        logger.info("✓ 服务器已停止")


async def _run_all(
    config_path: Path,
    testing: bool,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    web_port: int,
):
    """同时运行 Web + Gateway"""
    import uvicorn
    from agent.app import build_app_async, build_channel_apps_async
    from agent.gateway.server import GatewayServer
    from agent.scheduler import HeartbeatScheduler, CronScheduler

    # 1. 构建 Agent 应用
    logger.info("正在加载 Agent 应用...")
    settings, apps = await build_channel_apps_async(config_path, testing=testing)
    app_instance = apps.get("default") or next(iter(apps.values()))
    workspace_dir = settings.agent.workspace_dir
    logger.info("✓ Agent 应用加载完成")

    # 2. 初始化 Gateway
    gateway = GatewayServer(app_instance)
    for runtime_id, app in apps.items():
        gateway.register_runtime_app(runtime_id, app)

    multi_runtime_mode = bool(settings.channels and settings.channels.instances)

    # 3. 注册飞书通道
    if enable_feishu and not testing:
        if multi_runtime_mode:
            for instance in settings.channels.instances:
                if instance.channel_type != "feishu":
                    logger.warning("暂不支持的渠道类型: %s", instance.channel_type)
                    continue

                feishu_app_id = os.environ.get(instance.app_id_env or "")
                feishu_app_secret = os.environ.get(instance.app_secret_env or "")
                if not feishu_app_id or not feishu_app_secret:
                    logger.warning(
                        "渠道实例 %s 缺少飞书凭证环境变量：%s / %s",
                        instance.name,
                        instance.app_id_env,
                        instance.app_secret_env,
                    )
                    continue

                try:
                    await gateway.register_feishu(
                        feishu_app_id,
                        feishu_app_secret,
                        adapter_id=instance.name,
                    )
                    logger.info("✓ 飞书实例已连接: %s", instance.name)
                except Exception as e:
                    logger.warning(f"飞书实例 {instance.name} 连接失败: {e}")
        else:
            feishu_app_id = os.environ.get("FEISHU_APP_ID")
            feishu_app_secret = os.environ.get("FEISHU_APP_SECRET")

            if feishu_app_id and feishu_app_secret:
                try:
                    await gateway.register_feishu(feishu_app_id, feishu_app_secret)
                    logger.info("✓ 飞书通道已连接")
                except Exception as e:
                    logger.warning(f"飞书通道连接失败: {e}")

    if multi_runtime_mode and (enable_heartbeat or enable_cron):
        logger.warning("多 runtime 模式下暂未支持 Heartbeat/Cron 扇出，已自动禁用")
        enable_heartbeat = False
        enable_cron = False

    # 4. 启动调度器
    heartbeat_scheduler = None
    cron_scheduler = None

    if enable_heartbeat:
        heartbeat_scheduler = HeartbeatScheduler(
            workspace_dir=workspace_dir,
            agent_core=app_instance.core,
            gateway=gateway,
            interval_minutes=heartbeat_interval,
        )
        await heartbeat_scheduler.start()
        logger.info("✓ Heartbeat 调度器已启动")

    if enable_cron:
        cron_scheduler = CronScheduler(
            config_path=workspace_dir / "CRON.yaml",
            workspace_dir=workspace_dir,
            agent_core=app_instance.core,
            gateway=gateway,
        )
        await cron_scheduler.start()
        logger.info("✓ Cron 调度器已启动")

    # 5. 启动 Web UI（在后台任务中）
    from agent.web.app import create_app

    web_app = create_app(app_instance, testing=testing)

    # 6. 同时运行 Gateway 和 Web
    logger.info("=" * 60)
    logger.info(f"Web UI: http://127.0.0.1:{web_port}")
    logger.info("Gateway + Heartbeat + Cron 运行中...")
    logger.info("按 Ctrl+C 停止所有服务")
    logger.info("=" * 60)

    # 创建 uvicorn 配置
    config = uvicorn.Config(
        web_app,
        host="127.0.0.1",
        port=web_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    try:
        # 在后台启动 Web 服务器
        web_task = asyncio.create_task(server.serve())

        # 启动 Gateway（主循环）
        gateway_task = asyncio.create_task(gateway.start())

        # 等待任一任务完成或被中断
        await asyncio.gather(web_task, gateway_task)

    except KeyboardInterrupt:
        logger.info("\n收到停止信号，正在关闭...")
    finally:
        # 清理资源
        if heartbeat_scheduler:
            await heartbeat_scheduler.stop()
        if cron_scheduler:
            await cron_scheduler.stop()
        await gateway.stop()
        logger.info("✓ 所有服务已停止")


if __name__ == "__main__":
    main()
