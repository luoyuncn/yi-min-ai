# tests/test_config_proactive.py
from pathlib import Path

import pytest

from agent.config.loader import ConfigError, load_settings


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
        "  channel: feishu\n"
        "  channel_instance: oc_default_chat\n",
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
    assert settings.proactive.channel_instance == "oc_default_chat"


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


def test_load_settings_proactive_defaults_when_empty_section(tmp_path: Path) -> None:
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
        "proactive: {}\n",
        encoding="utf-8",
    )

    settings = load_settings(agent_yaml)

    assert settings.proactive is not None
    assert settings.proactive.enabled is False
    assert settings.proactive.min_interval_minutes == 20
    assert settings.proactive.max_interval_minutes == 90
    assert settings.proactive.quiet_hours is None
    assert settings.proactive.session_id == ""
    assert settings.proactive.channel == "feishu"
    assert settings.proactive.channel_instance == "default"


def test_proactive_quiet_hours_rejects_non_list(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n  - name: qwen\n    type: openai\n    model: qwen-max\n    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n  name: Yi Min\n  workspace_dir: ../workspace\n  max_iterations: 8\n"
        "providers:\n  config_file: providers.yaml\n  default_primary: qwen\n"
        "proactive:\n  quiet_hours: all_day\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be a list"):
        load_settings(agent_yaml)


def test_proactive_quiet_hours_rejects_out_of_range(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n  - name: qwen\n    type: openai\n    model: qwen-max\n    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n  name: Yi Min\n  workspace_dir: ../workspace\n  max_iterations: 8\n"
        "providers:\n  config_file: providers.yaml\n  default_primary: qwen\n"
        "proactive:\n  quiet_hours: [24]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"must be in \[0, 24\)"):
        load_settings(agent_yaml)


def test_proactive_min_greater_than_max_raises(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n  - name: qwen\n    type: openai\n    model: qwen-max\n    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n  name: Yi Min\n  workspace_dir: ../workspace\n  max_iterations: 8\n"
        "providers:\n  config_file: providers.yaml\n  default_primary: qwen\n"
        "proactive:\n  min_interval_minutes: 90\n  max_interval_minutes: 20\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="min_interval_minutes must be"):
        load_settings(agent_yaml)
