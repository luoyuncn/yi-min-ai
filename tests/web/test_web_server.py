"""FastAPI Web App 测试。"""

from fastapi.testclient import TestClient

from agent.web.app import create_web_app
from tests.web.support import write_testing_config


def test_web_app_serves_health_and_html(tmp_path) -> None:
    """Web app 至少应暴露健康检查和 HTML 入口。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    assert "text/html" in client.get("/").headers["content-type"]
