"""Compose immutable snapshot references for one account without rewriting prices."""
from investment_lab.common import atomic_write, digest, encoded, now


def validate_composed_selection(manifest, config):
    combination = manifest.get("composition")
    if not combination:
        return
    if config.start < combination["start"] or config.end > combination["end"]:
        raise ValueError(f"所选快照共同覆盖区间为 {combination['start']} 至 {combination['end']}，请调整回测日期")
    selected = [*config.symbols, *([config.benchmark] if config.benchmark else [])]
    if any(s not in manifest["securities"] for s in selected):
        raise ValueError("所选证券或基准不在组合快照内")


def compose_portable_manifests(parents):
    """Build the same deterministic composition manifest from portable source manifests."""
    if not isinstance(parents, list) or not 1 <= len(parents) <= 200:
        raise ValueError("请选择1至200个数据快照")
    if any(not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str) or
           not isinstance(item[1], dict) for item in parents):
        raise ValueError("数据快照清单格式无效")
    parents = sorted(parents, key=lambda item: item[0])
    snapshots = [snapshot for snapshot, _ in parents]
    if len(set(snapshots)) != len(snapshots):
        raise ValueError("数据快照必须是非重复的快照ID")
    if len(parents) == 1:
        return parents[0][0], parents[0][1]
    if len({m["synthetic"] for _, m in parents}) != 1:
        raise ValueError("合成演示与真实来源不能用于同一次回测，请取消其中一类")

    securities, partitions, actions, sessions, origins = {}, [], [], set(), {}
    scopes, providers, identities = set(), set(), {}
    for snapshot, manifest in parents:
        if not manifest["sessions"] or not manifest["securities"]:
            raise ValueError(f"{manifest['name']}：快照没有交易日历或标的")
        providers.add(manifest.get("source", {}).get("provider"))
        for symbol, security in manifest["securities"].items():
            identity = (security.get("market"), security.get("universe_id") or symbol)
            if symbol in securities or identity in identities:
                previous = origins.get(symbol) or identities[identity]
                raise ValueError(f"重复标的 {identity[1]}：{previous} 与 {manifest['name']}。每个标的请只勾选一份行情来源")
            scope = tuple(security.get(k) for k in ("market", "currency", "timezone"))
            if not all(scope):
                raise ValueError(f"{symbol}：缺少市场、币种或时区")
            scopes.add(scope)
            securities[symbol] = security
            origins[symbol] = manifest["name"]
            identities[identity] = manifest["name"]
        partitions.extend(manifest["partitions"])
        actions.extend(manifest["actions"])
        sessions.update(manifest["sessions"])
    if len(scopes) != 1:
        raise ValueError("同一次回测只允许同一市场、币种和时区；请分别选择这些快照")
    if "Yahoo/chart" in providers and len(providers) != 1:
        raise ValueError("Yahoo参考价格与其他价格来源不能混合，请选择相同价格口径的快照")

    # Union retains missing-session evidence. Never intersect calendars and hide gaps.
    # Each parent also bounds the usable interval, including nested compositions.
    bounds = []
    for _, manifest in parents:
        previous = manifest.get("composition", {})
        bounds.append((previous.get("start", min(manifest["sessions"])),
                       previous.get("end", max(manifest["sessions"]))))
    start, end = max(b[0] for b in bounds), min(b[1] for b in bounds)
    if len([d for d in sessions if start <= d <= end]) < 2:
        raise ValueError("所选快照没有至少两个交易日的共同覆盖区间")
    source = {"provider": next(iter(providers)) if len(providers) == 1 else "composite",
              "parents": [{"snapshot": s, "source": m.get("source", {})} for s, m in parents]}
    manifest = {
        "schema": 1, "name": f"组合快照 · {len(parents)}个来源", "securities": securities,
        "partitions": sorted(partitions, key=lambda p: (p["symbol"], p["year"], p["hash"])),
        "actions": sorted(actions, key=encoded), "sessions": sorted(sessions), "source": source,
        "synthetic": parents[0][1]["synthetic"], "raw": None,
        "composition": {"version": 1, "start": start, "end": end,
                        "calendar_rule": "所选日历取并集，保留缺口；运行区间不得超出任一来源覆盖范围",
                        "parents": [{"snapshot": s, "name": m["name"]} for s, m in parents]}
    }
    return digest(manifest), manifest


def compose_snapshots(store, snapshots):
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 200:
        raise ValueError("请选择1至200个数据快照")
    if any(not isinstance(s, str) for s in snapshots) or len(set(snapshots)) != len(snapshots):
        raise ValueError("数据快照必须是非重复的快照ID")
    parents = [(snapshot, store.manifest(snapshot)) for snapshot in snapshots]
    snapshot, manifest = compose_portable_manifests(parents)
    if len(parents) == 1:
        return snapshot
    with store.lock(), store.connect() as cx:
        atomic_write(store.root / "manifests" / (snapshot + ".json"), manifest)
        inserted = cx.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?)",
                              (snapshot, now(), encoded(manifest).decode())).rowcount
        if inserted:
            store.audit("snapshot_composition", {"snapshot": snapshot, "parents": snapshots}, cx)
    # No heads entry: run-specific combinations do not clutter the source picker.
    return snapshot
