import json
import pytest

from scripts.build_pages_site import RUNTIME_FILES, build


def test_pages_build_contains_allowlisted_app_and_engine_without_local_data(tmp_path):
    output = tmp_path / "pages"
    manifest = build(output)
    assert (output / "index.html").is_file()
    assert (output / "app.js").is_file()
    assert (output / "worker.js").is_file()
    assert all((output / "runtime/investment_lab" / name).is_file() for name in RUNTIME_FILES)
    assert "investment_lab_snapshot" not in json.dumps(manifest)
    assert not (output / "local_config").exists()
    assert not (output / "runs").exists()
    assert not (output / "user_strategies").exists()
    assert len(manifest["files"]) == 4 + 1 + len(RUNTIME_FILES)


def test_pages_build_refuses_to_publish_unlisted_existing_files(tmp_path):
    output = tmp_path / "pages"
    build(output)
    unexpected = output / "local-private-file.txt"
    unexpected.write_text("must not enter a Pages artifact", encoding="utf-8")

    with pytest.raises(ValueError, match="白名单外文件"):
        build(output)

    assert unexpected.read_text(encoding="utf-8") == "must not enter a Pages artifact"
