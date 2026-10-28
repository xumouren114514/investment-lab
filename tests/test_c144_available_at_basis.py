"""C-144（§14 第 4 条）：`available_at` 是按交易日推的保守近似，必须被写出来。

用户裁决：诚实标注、不改架构（没有真实可获得时刻的数据源）。这里钉三处：
新快照的 manifest 带 `available_at_basis="trade_date"`、组合快照继承它、数据页返回它。
"""
from copy import deepcopy

from investment_lab.data.compose import compose_portable_manifests
from investment_lab.data.store import Store


def test_new_snapshot_declares_trade_date_basis(tmp_path, small):
    store = Store(tmp_path)
    snapshot = store.ingest(**small)
    assert store.manifest(snapshot)["available_at_basis"] == "trade_date"


def test_composition_inherits_basis(small, tmp_path):
    store = Store(tmp_path)
    first = store.ingest(**small)
    second_payload = deepcopy(small)
    second_payload["name"] = "second"
    second_payload["securities"] = {"B": {**second_payload["securities"]["A"], "name": "第二标的"}}
    for bar in second_payload["bars"]:
        bar.update(symbol="B")
    second = store.ingest(**second_payload)
    _, manifest = compose_portable_manifests([(first, store.manifest(first)),
                                              (second, store.manifest(second))])
    assert manifest["available_at_basis"] == "trade_date"


def test_coverage_endpoint_surfaces_basis(tmp_path, small):
    from fastapi.testclient import TestClient

    from investment_lab.web.app import create_app
    root = tmp_path / "data"
    store = Store(root)
    snapshot = store.ingest(**small)
    app = create_app(root)
    with TestClient(app) as client:
        body = client.get(f"/api/coverage/{snapshot}").json()
    assert body["available_at_basis"] == "trade_date", body.keys()
