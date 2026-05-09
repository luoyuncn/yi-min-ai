# tests/test_config_proactive.py
from pathlib import Path
from agent.config.loader import load_settings


def test_load_settings_parses_proactive_section(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen-max\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n"
        "proactive:\n"
        "  enabled: true\n"
        "  min_interval_minutes: 15\n"
        "  max_interval_minutes: 60\n"
        "  quiet_hours: [0, 1, 2, 3]\n"
        "  session_id: oc_test123\n"
        "  channel: feishu\n",
        encoding="utf-8",
    )

    settings = load_settings(agent_yaml)

    assert settings.proactive is not None
    assert settings.proactive.enabled is True
    assert settings.proactive.min_interval_minutes == 15
    assert settings.proactive.max_interval_minutes == 60
    assert settings.proactive.quiet_hours == [0, 1, 2, 3]
    assert settings.proactive.session_id == "oc_test123"
    assert settings.proactive.channel == "feishu"


def test_load_settings_proactive_defaults_to_none(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen-max\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n",
        encoding="utf-8",
    )

    settings = load_settings(agent_yaml)

    assert settings.proactive is None
