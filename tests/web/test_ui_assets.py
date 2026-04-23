"""Web UI 资源测试。"""

from fastapi.testclient import TestClient

from agent.web.app import create_web_app
from tests.web.support import write_testing_config


def test_root_page_contains_agent_ui_hooks(tmp_path) -> None:
    """首页 HTML 应至少返回一个可用的 Web UI。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "yi-min-ai" in response.text
    assert (
        ("copilotkit-console" in response.text and "/assets/app.js" in response.text)
        or ("yi-min-ai Agent Console" in response.text and "Agent Timeline" in response.text)
    )
