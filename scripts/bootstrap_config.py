"""Create a local universe configuration without replacing an existing one."""
from pathlib import Path


def ensure_universe_config(project_root: Path) -> bool:
    """Copy the generic example once; return False when a local config exists."""
    config_dir = Path(project_root) / "config"
    target = config_dir / "universe.yaml"
    if target.exists():
        return False

    example = config_dir / "universe.example.yaml"
    if not example.is_file():
        raise FileNotFoundError(f"找不到通用标的池模板：{example}")
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as destination:
            destination.write(example.read_bytes())
    except FileExistsError:
        # Another app start may have created it; never replace that config.
        return False
    return True


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    created = ensure_universe_config(root)
    print("已创建本机标的池配置。" if created else "本机标的池配置已存在，未覆盖。")
