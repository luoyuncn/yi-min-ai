"""Gateway 多通道启动入口 - 支持飞书 + Heartbeat + Cron"""

import asyncio
import logging
import os
from pathlib import Path

import click

from agent.app import build_app
from agent.gateway.server import GatewayServer
from agent.observability.logging import setup_logging
from agent.scheduler import HeartbeatScheduler, CronScheduler

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/agent.yaml",
    help="配置文件路径",
)
@click.option(
    "--enable-feishu/--no-feishu",
    default=True,
    help="是否启用飞书通道（默认启用）",
)
@click.option(
    "--enable-heartbeat/--no-heartbeat",
    default=False,
    help="是否启用 Heartbeat 调度（默认禁用）",
)
@click.option(
    "--heartbeat-interval",
    type=int,
    default=30,
    help="Heartbeat 间隔（分钟，默认 30）",
)
@click.option(
    "--enable-cron/--no-cron",
    default=False,
    help="是否启用 Cron 调度（默认禁用）",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    default="INFO",
    help="日志级别",
)
def main(
    config: str,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    log_level: str,
):
    """启动 Gateway 多通道服务器（飞书 + Heartbeat + Cron）

    \b
    使用示例：
        # 仅启动飞书通道
        uv run python -m agent.gateway.main

        # 启用飞书 + Heartbeat（每 30 分钟）
        uv run python -m agent.gateway.main --enable-heartbeat

        # 启用飞书 + Heartbeat + Cron
        uv run python -m agent.gateway.main --enable-heartbeat --enable-cron

        # 自定义 Heartbeat 间隔（每 10 分钟）
        uv run python -m agent.gateway.main --enable-heartbeat --heartbeat-interval 10

    \b
    环境变量要求：
        FEISHU_APP_ID      - 飞书应用 ID（必需）
        FEISHU_APP_SECRET  - 飞书应用密钥（必需）
    """
    asyncio.run(run_server(
        config_path=Path(config),
        enable_feishu=enable_feishu,
        enable_heartbeat=enable_heartbeat,
        heartbeat_interval=heartbeat_interval,
        enable_cron=enable_cron,
        log_level=log_level,
    ))


async def run_server(
    config_path: Path,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    log_level: str,
):
    """异步运行 Gateway 服务器"""

    # 1. 初始化日志
    workspace_dir = Path("workspace")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = workspace_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(
        log_file=logs_dir / "gateway.log",
        level=log_level,
    )

    logger.info("=" * 60)
    logger.info("Gateway 多通道服务器启动")
    logger.info("=" * 60)

    # 2. 构建 Agent 应用
    from agent.app import build_app_async
    
    logger.info("正在加载 Agent 应用...")
    app = await build_app_async(config_path, testing=False)
    logger.info("✓ Agent 应用加载完成")

    # 3. 初始化 Gateway
    gateway = GatewayServer(app)

    # 4. 注册飞书通道
    if enable_feishu:
        feishu_app_id = os.environ.get("FEISHU_APP_ID")
        feishu_app_secret = os.environ.get("FEISHU_APP_SECRET")

        if not feishu_app_id or not feishu_app_secret:
            logger.error(
                "飞书通道需要设置环境变量：\n"
                "  export FEISHU_APP_ID=your-app-id\n"
                "  export FEISHU_APP_SECRET=your-app-secret"
            )
            return

        logger.info(f"正在连接飞书（APP_ID: {feishu_app_id[:10]}...）")
        try:
            await gateway.register_feishu(feishu_app_id, feishu_app_secret)
            logger.info("✓ 飞书通道已连接")
        except Exception as e:
            logger.error(f"✗ 飞书通道连接失败: {e}")
            logger.warning("将继续运行，但飞书通道不可用")

    # 5. 启动 Heartbeat（可选）
    heartbeat_scheduler = None
    if enable_heartbeat:
        logger.info(f"启动 Heartbeat 调度器（间隔: {heartbeat_interval} 分钟）")
        heartbeat_scheduler = HeartbeatScheduler(
            workspace_dir=workspace_dir,
            agent_core=app.core,
            gateway=gateway,
            interval_minutes=heartbeat_interval,
        )
        await heartbeat_scheduler.start()
        logger.info("✓ Heartbeat 调度器已启动")

    # 6. 启动 Cron（可选）
    cron_scheduler = None
    if enable_cron:
        logger.info("启动 Cron 调度器")
        cron_scheduler = CronScheduler(
            config_path=workspace_dir / "CRON.yaml",
            workspace_dir=workspace_dir,
            agent_core=app.core,
            gateway=gateway,
        )
        await cron_scheduler.start()
        logger.info("✓ Cron 调度器已启动")

    # 7. 启动 Gateway 主循环
    logger.info("=" * 60)
    logger.info("Gateway 服务器运行中...")
    logger.info("按 Ctrl+C 停止服务器")
    logger.info("=" * 60)

    try:
        await gateway.start()
    except KeyboardInterrupt:
        logger.info("\n收到停止信号，正在关闭...")
    finally:
        # 8. 清理资源
        if heartbeat_scheduler:
            await heartbeat_scheduler.stop()
            logger.info("✓ Heartbeat 调度器已停止")

        if cron_scheduler:
            await cron_scheduler.stop()
            logger.info("✓ Cron 调度器已停止")

        await gateway.stop()
        logger.info("✓ Gateway 服务器已停止")


if __name__ == "__main__":
    main()
