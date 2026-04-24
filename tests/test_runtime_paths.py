from pathlib import Path

from agent.runtime_paths import resolve_base_workspace


def test_resolve_base_workspace_uses_configured_workspace_dir(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    state_root = tmp_path / "state"
    config_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ${YIMIN_DATA_ROOT:-../fallback}/default\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: gpt-5\n"
        "    type: openai\n"
        "    model: gpt-5.4\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("YIMIN_DATA_ROOT", str(state_root))

    assert resolve_base_workspace(config_dir / "agent.yaml") == (state_root / "default").resolve()
