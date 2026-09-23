from scripts.bootstrap_config import ensure_universe_config


def test_bootstrap_copies_generic_config_once_and_preserves_custom_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example = config_dir / "universe.example.yaml"
    example.write_text("version: 1\ninstruments: []\n", encoding="utf-8")

    assert ensure_universe_config(tmp_path) is True
    target = config_dir / "universe.yaml"
    assert target.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")

    target.write_text("user: custom\n", encoding="utf-8")
    assert ensure_universe_config(tmp_path) is False
    assert target.read_text(encoding="utf-8") == "user: custom\n"
