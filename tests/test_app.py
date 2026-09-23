from dataclasses import asdict
import asyncio
import gzip
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from investment_lab.common import atomic_write, digest
from investment_lab.jobs import JobManager, create_run, execute_run
from investment_lab.web.app import create_app


def test_local_api_boundary_and_strategy_revision(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/docs/OPERATIONS.md").status_code == 200
        assert client.get("/api/docs/ACCEPTANCE.md").status_code == 400
        assert client.get("/api/status").json()["real_datasets"] == 0
        assert client.post("/api/strategy", json={"name":"a.py","code":"x=1"}).status_code == 403
        headers = {"X-Lab-Request":"local-ui"}
        assert client.post("/api/strategy", headers=headers, json={"name":"../outside.py","code":"x=1"}).status_code == 400
        assert client.post("/api/strategy", headers=headers, json={"name":"a.py","code":"x=1"}).status_code == 200
        r = client.post("/api/strategy", headers=headers, json={"name":"a.py","code":"x=2"})
        assert r.json()["previous_snapshot"]
        assert client.post("/api/update", headers={**headers,"Origin":"https://external.example"}, json={}).status_code == 403
        assert client.post("/api/update", headers=headers, json={}).json()["status"] == "needs_configuration"


def test_snapshot_export_is_verified_gzip_and_excludes_raw_payload(tmp_path, small):
    app = create_app(tmp_path / "data")
    store = app.state.store
    payload = {**small, "raw": {"provider_response": "private-source-payload"}}
    snapshot = store.ingest(**payload)
    with TestClient(app) as client:
        response = client.get(f"/api/exports/{snapshot}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    assert f"investment-lab-{snapshot}.json.gz" in response.headers["content-disposition"]
    package = json.loads(gzip.decompress(response.content))
    assert package["format"] == "investment-lab-snapshot"
    assert package["format_version"] == 1
    assert package["snapshot_id"] == snapshot
    assert digest(package["manifest"]) == snapshot
    assert package["bars"] == small["bars"]
    assert package["manifest"]["raw"]
    assert "provider_response" not in json.dumps(package)
    assert b"private-source-payload" not in response.content


@pytest.mark.parametrize("credential_location", ["manifest", "bar"])
def test_snapshot_export_blocks_credential_fields_without_echoing_secret(tmp_path, small, credential_location):
    app = create_app(tmp_path / "data")
    payload = {**small}
    if credential_location == "manifest":
        payload["source"] = {"provider": "custom", "api_token": "do-not-export-this-test-value"}
    else:
        payload["bars"] = [{**small["bars"][0], "metadata": {"secret": "do-not-export-this-test-value"}}, *small["bars"][1:]]
    snapshot = app.state.store.ingest(**payload)
    with TestClient(app) as client:
        response = client.get(f"/api/exports/{snapshot}")
    assert response.status_code == 400
    assert "已阻止导出" in response.json()["detail"]
    assert "do-not-export-this-test-value" not in response.text


def test_snapshot_import_verifies_manifest_and_rebuilds_without_raw_response(tmp_path, small):
    source = create_app(tmp_path / "source")
    source_payload = {**small, "raw": {"provider_response": "not-exported"}}
    original = source.state.store.ingest(**source_payload)
    with TestClient(source) as client:
        package_response = client.get(f"/api/exports/{original}")
    package = json.loads(gzip.decompress(package_response.content))

    destination = create_app(tmp_path / "destination")
    headers = {"X-Lab-Request": "local-ui"}
    with TestClient(destination) as client:
        imported = client.post("/api/imports", headers=headers, json=package)
        assert imported.status_code == 200
        result = imported.json()
        assert client.get("/api/datasets").json()[0]["id"] == result["snapshot"]
        manifest, bars = destination.state.store.load(result["snapshot"])
        assert manifest["raw"] is None
        assert bars == small["bars"]
        tampered = {**package, "snapshot_id": "0" * 64}
        assert client.post("/api/imports", headers=headers, json=tampered).status_code == 400


def test_snapshot_import_rejects_credentials_and_composition(tmp_path, small):
    app = create_app(tmp_path / "data")
    headers = {"X-Lab-Request": "local-ui"}
    snapshot = app.state.store.ingest(**small)
    manifest = app.state.store.manifest(snapshot)
    bars = app.state.store.load(snapshot)[1]
    package = {"format": "investment-lab-snapshot", "format_version": 1, "snapshot_id": snapshot,
               "manifest": manifest, "bars": bars}
    with TestClient(app) as client:
        credential_package = {**package, "bars": [{**bars[0], "token": "not-for-storage"}, *bars[1:]]}
        assert client.post("/api/imports", headers=headers, json=credential_package).status_code == 400
        composed_manifest = {**manifest, "composition": {"version": 1}}
        composed = {**package, "manifest": composed_manifest, "snapshot_id": digest(composed_manifest)}
        assert client.post("/api/imports", headers=headers, json=composed).status_code == 400


def test_snapshot_import_caps_content_length_and_chunked_bodies():
    from investment_lab.web.app import ImportBodyLimitMiddleware

    async def check(scope, messages):
        downstream_called = False
        sent = []

        async def downstream(_scope, _receive, _send):
            nonlocal downstream_called
            downstream_called = True

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        middleware = ImportBodyLimitMiddleware(downstream, max_bytes=5)
        await middleware(scope, receive, send)
        assert not downstream_called
        assert sent[0]["status"] == 413

    path = {"type": "http", "method": "POST", "path": "/api/imports"}
    asyncio.run(check({**path, "headers": [(b"content-length", b"6")]}, []))
    asyncio.run(check({**path, "headers": []}, [
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ]))


def test_open_download_is_local_bounded_and_prevents_duplicate_worker(tmp_path, monkeypatch):
    import investment_lab.web.app as module
    commands = []
    class Process:
        pid = 123
        def __init__(self, command, **kwargs):
            commands.append(command)
        def poll(self):
            return None
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        response = client.get("/api/open-resources").json()
        assert response["catalog"] and not response["active"]
        headers = {"X-Lab-Request": "local-ui"}
        assert client.post("/api/open-resources", json={"scope": "sample"}).status_code == 403
        assert client.post("/api/open-resources", headers=headers, json={"scope": "arbitrary-command"}).status_code == 400
        assert client.post("/api/open-resources", headers=headers, json={"scope": "sample"}).status_code == 200
        assert commands[0][-3:] == ["fetch-open-data", "--scope", "sample"]
        assert client.get("/api/open-resources").json()["active"]
        assert client.post("/api/open-resources", headers=headers, json={"scope": "sample"}).status_code == 409
        assert len(commands) == 1


def test_failed_reference_run_keeps_readable_error(tmp_path, small, config):
    app = create_app(tmp_path / "data")
    store = app.state.store
    reason = "Yahoo 历史价格为拆股调整参考序列，尚未还原真实交易价；仅可查阅"
    small["securities"]["A"]["execution_blocked"] = reason
    run_id = create_run(store, {"snapshot": store.ingest(**small), "config": asdict(config)})
    with pytest.raises(ValueError, match="仅可查阅"):
        execute_run(store, run_id)
    with TestClient(app) as client:
        detail = client.get(f"/api/runs/{run_id}").json()
        listing = client.get("/api/runs").json()[0]
    assert detail["status"] == listing["status"] == "failed"
    assert detail["error"]["message"] == listing["summary"]["message"] == f"A: {reason}"
    assert "result" not in detail


def test_abnormal_worker_exit_is_visible_without_error_file(tmp_path, small, config):
    app = create_app(tmp_path / "data")
    store = app.state.store
    run_id = create_run(store, {"snapshot": store.ingest(**small), "config": asdict(config)})
    JobManager(store)._watch(run_id, SimpleNamespace(poll=lambda: 7, returncode=7), 600, 2048)
    assert not (store.root / "runs" / run_id / "error.json").exists()
    with TestClient(app) as client:
        detail = client.get(f"/api/runs/{run_id}").json()
        assert client.get("/api/runs/missing").status_code == 404
    assert detail["status"] == "failed"
    assert detail["error"] == {"message": "工作进程异常退出", "exit_code": 7}


def test_pending_and_cancelled_run_details_are_distinct(tmp_path, small, config):
    app = create_app(tmp_path / "data")
    store = app.state.store
    run_id = create_run(store, {"snapshot": store.ingest(**small), "config": asdict(config)})
    with TestClient(app) as client:
        pending = client.get(f"/api/runs/{run_id}").json()
        assert pending["status"] == "queued" and pending["error"] is None
        atomic_write(store.root / "runs" / run_id / "progress.json", {"done": 2, "total": 5})
        with store.connect() as cx:
            cx.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run_id,))
        atomic_write(store.root / "runs" / run_id / "error.json", {"message": "用户取消"})
        cancelled = client.get(f"/api/runs/{run_id}").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"]["message"] == "用户取消"
    assert cancelled["progress"]["done"] == 2
