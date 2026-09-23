import yaml
from investment_lab.common import PROJECT, atomic_write, digest, now, read_json


def universe(store):
    path = store.root / "local_config" / "universe.json"
    if path.exists():
        return read_json(path)
    configured = PROJECT / "config/universe.yaml"
    example = PROJECT / "config/universe.example.yaml"
    source = configured if configured.exists() else example
    return yaml.safe_load(source.read_text(encoding="utf-8"))


def update_universe(store, instrument):
    from datetime import date
    for field in ("id", "code", "name", "market", "currency", "kind", "effective", "active"):
        if field not in instrument:
            raise ValueError("标的缺少字段 " + field)
    if instrument["market"] not in ("US", "HK", "CN") or instrument["currency"] != {"US": "USD", "HK": "HKD", "CN": "CNY"}[instrument["market"]]:
        raise ValueError("市场/币种不符")
    if instrument["kind"] not in ("stock", "etf", "index", "leveraged_etf", "future") or instrument.get("inverse"):
        raise ValueError("无效品种或反向产品")
    date.fromisoformat(instrument["effective"])
    with store.lock(), store.connect() as cx:
        value = universe(store)
        old_hash = store.put_object("raw", value)
        value["instruments"] = [i for i in value["instruments"] if i["id"] != instrument["id"]] + [instrument]
        value["version"] += 1
        value["updated"] = now()
        new_hash = store.put_object("raw", value)
        atomic_write(store.root / "local_config" / "universe.json", value)
        store.audit("universe_change", {"previous": old_hash, "current": new_hash, "instrument": instrument["id"], "effective": instrument["effective"]}, cx)
    return value
