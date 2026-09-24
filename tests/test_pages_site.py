import json
import pytest

from scripts.build_pages_site import CATALOG_PAGE_ASSET, PAGE_ASSETS, RUNTIME_FILES, SHARED_PAGE_ASSETS, build


def test_pages_build_contains_allowlisted_app_and_engine_without_local_data(tmp_path):
    output = tmp_path / "pages"
    manifest = build(output)
    assert (output / "index.html").is_file()
    assert (output / "app.js").is_file()
    assert (output / "worker.js").is_file()
    assert (output / "ui-utils.js").is_file()
    assert (output / "strategy-forms.js").is_file()
    assert (output / "strategy-catalog.json").read_bytes() == CATALOG_PAGE_ASSET.read_bytes()
    assert (output / "ui-utils.js").read_bytes() == next(iter(SHARED_PAGE_ASSETS.values())).read_bytes()
    assert (output / "strategy-forms.js").read_bytes() == SHARED_PAGE_ASSETS["strategy-forms.js"].read_bytes()
    assert all((output / "runtime/investment_lab" / name).is_file() for name in RUNTIME_FILES)
    assert (output / "runtime/investment_lab/strategies/catalog.json").read_bytes() == CATALOG_PAGE_ASSET.read_bytes()
    assert "investment_lab_snapshot" not in json.dumps(manifest)
    assert not (output / "local_config").exists()
    assert not (output / "runs").exists()
    assert not (output / "user_strategies").exists()
    assert "更简单的常用设置" not in (output / "index.html").read_text(encoding="utf-8")
    assert "strategy-catalog.json" in (output / "app.js").read_text(encoding="utf-8")
    assert "validate_params" in (output / "worker.js").read_text(encoding="utf-8")
    assert "monthly_equal_weight" in (output / "runtime/investment_lab/strategies/examples.py").read_text(encoding="utf-8")
    assert len(manifest["files"]) == len(PAGE_ASSETS) + len(SHARED_PAGE_ASSETS) + 2 + len(RUNTIME_FILES)


def test_pages_build_refuses_to_publish_unlisted_existing_files(tmp_path):
    output = tmp_path / "pages"
    build(output)
    unexpected = output / "local-private-file.txt"
    unexpected.write_text("must not enter a Pages artifact", encoding="utf-8")

    with pytest.raises(ValueError, match="白名单外文件"):
        build(output)

    assert unexpected.read_text(encoding="utf-8") == "must not enter a Pages artifact"
