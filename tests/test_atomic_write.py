import pytest

from investment_lab import common


def test_transient_windows_sharing_error_preserves_old_file_until_replaced(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    common.atomic_write(path, {"done": 1})
    replace = common.os.replace
    busy = iter((True, True, False))

    def replacement(source, destination):
        assert common.read_json(destination) == {"done": 1}
        assert common.read_json(source) == {"done": 2}
        if next(busy):
            exc = PermissionError("sharing violation")
            exc.winerror = 32
            raise exc
        replace(source, destination)

    monkeypatch.setattr(common.os, "replace", replacement)
    monkeypatch.setattr(common.time, "sleep", lambda delay: None)
    common.atomic_write(path, {"done": 2})
    assert common.read_json(path) == {"done": 2}
    assert not list(tmp_path.glob("*.tmp"))


def test_permanent_windows_access_failure_still_raises_and_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    common.atomic_write(path, {"done": 1})

    def denied(source, destination):
        exc = PermissionError("access denied")
        exc.winerror = 5
        raise exc

    monkeypatch.setattr(common.os, "replace", denied)
    monkeypatch.setattr(common.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        common.atomic_write(path, {"done": 2})
    assert common.read_json(path) == {"done": 1}
    assert common.read_json(next(tmp_path.glob("*.tmp"))) == {"done": 2}
