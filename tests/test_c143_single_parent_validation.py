"""C-143（§14 第 6 条）：单父组合不再绕过校验。

原来 `compose_portable_manifests` 在 `len(parents) == 1` 时直接返回父快照，
"只有 1 个交易日""标的缺市场/币种/时区"这类问题要到回测阶段才炸。
现在所有父快照都过同一套最低校验，单父额外要求≥2 个交易日，但**身份不变**。
"""
from copy import deepcopy

import pytest

from investment_lab.data.compose import compose_portable_manifests, compose_snapshots
from investment_lab.data.store import Store


def one_parent(manifest):
    return [(("f" * 64), manifest)]


def test_single_parent_with_one_session_is_rejected(small):
    payload = deepcopy(small)
    payload["sessions"] = payload["sessions"][:1]
    payload["bars"] = payload["bars"][:1]
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    with pytest.raises(ValueError, match="不足两个交易日"):
        compose_portable_manifests(one_parent(manifest))


def test_single_parent_missing_scope_is_rejected(small):
    manifest = {k: v for k, v in deepcopy(small).items() if k != "bars"}
    del manifest["securities"]["A"]["timezone"]
    with pytest.raises(ValueError, match="缺少市场、币种或时区"):
        compose_portable_manifests(one_parent(manifest))


def test_single_parent_missing_calendar_or_securities_is_rejected(small):
    manifest = {k: v for k, v in deepcopy(small).items() if k != "bars"}
    manifest["sessions"] = []
    with pytest.raises(ValueError, match="没有交易日历或标的"):
        compose_portable_manifests(one_parent(manifest))


def test_single_parent_keeps_identity_and_manifest(tmp_path, small):
    store = Store(tmp_path)
    snapshot = store.ingest(**small)
    same, manifest = compose_portable_manifests([(snapshot, store.manifest(snapshot))])
    assert same == snapshot, "单父组合不该造出新快照 id"
    assert manifest == store.manifest(snapshot)
    assert compose_snapshots(store, [snapshot]) == snapshot


def test_single_parent_respects_nested_composition_bounds(small):
    """父快照本身是组合（有 composition.start/end）时，可用区间按它算。"""
    manifest = {k: v for k, v in deepcopy(small).items() if k != "bars"}
    manifest["composition"] = {"version": 1, "start": manifest["sessions"][0],
                               "end": manifest["sessions"][0], "parents": []}
    with pytest.raises(ValueError, match="不足两个交易日"):
        compose_portable_manifests(one_parent(manifest))
