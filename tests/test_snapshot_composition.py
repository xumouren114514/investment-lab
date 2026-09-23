from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest
from fastapi.testclient import TestClient

from investment_lab.data.compose import compose_portable_manifests, compose_snapshots
from investment_lab.data.store import Store
from investment_lab.engine.core import preflight
from investment_lab.engine.reference import REFERENCE_BASIS, YAHOO_BLOCK, reference_available
from investment_lab.jobs import create_run, execute_run, reproduce
from investment_lab.web.app import create_app


def other_symbol(small):
    other = deepcopy(small)
    other['name'] = 'second source'
    other['securities'] = {'B': {**other['securities']['A'], 'name': '第二标的'}}
    for bar in other['bars']:
        bar.update(symbol='B', open='20', high='22', low='18', close='20', vwap_value='20', amount='2000000')
    return other


def pair(store, small, other=None):
    return [store.ingest(**small), store.ingest(**(other or other_symbol(small)))]


def test_composition_reuses_immutable_partitions_and_preserves_provenance(tmp_path, small):
    store = Store(tmp_path)
    second = other_symbol(small)
    second['sessions'] = second['sessions'][1:]
    second['bars'] = second['bars'][1:]
    second['actions'] = [{'symbol': 'B', 'date': second['sessions'][1], 'type': 'dividend', 'amount': '0.2', 'pay_date': second['sessions'][2]}]
    ids = pair(store, small, second)
    before = {p.name: p.read_bytes() for p in (tmp_path / 'market').glob('*')}
    original = [store.manifest(s) for s in ids]
    combined = compose_snapshots(store, ids)
    portable_id, portable_manifest = compose_portable_manifests(list(zip(ids, original)))
    assert portable_id == combined
    assert portable_manifest == store.manifest(combined)
    assert combined == compose_snapshots(store, list(reversed(ids)))
    manifest, bars = store.load(combined)
    assert set(manifest['securities']) == {'A', 'B'}
    assert manifest['sessions'] == small['sessions']
    assert manifest['composition']['start'] == second['sessions'][0]
    assert {p['snapshot'] for p in manifest['composition']['parents']} == set(ids)
    assert manifest['actions'] == second['actions']
    assert len(bars) == len(small['bars']) + len(second['bars'])
    assert [store.manifest(s) for s in ids] == original
    assert {p.name: p.read_bytes() for p in (tmp_path / 'market').glob('*')} == before
    assert {d['id'] for d in store.datasets()} == set(ids)
    assert compose_snapshots(store, [ids[0]]) == ids[0]


@pytest.mark.parametrize('case,reason', [
    ('synthetic', '合成演示'), ('market', '同一市场'), ('currency', '币种'),
    ('timezone', '时区'), ('duplicate', '重复标的'), ('alias', '重复标的'), ('provider', '参考价格'),
    ('disjoint', '共同覆盖'), ('empty', '交易日历'),
])
def test_incompatible_sources_are_rejected_before_a_run(tmp_path, small, case, reason):
    store = Store(tmp_path)
    second = other_symbol(small)
    if case == 'synthetic':
        second['synthetic'] = False
    elif case in ('market', 'currency', 'timezone'):
        second['securities']['B'][case] = {'market': 'HK', 'currency': 'HKD', 'timezone': 'Asia/Hong_Kong'}[case]
    elif case == 'duplicate':
        second = {**deepcopy(small), 'name': 'another version'}
    elif case == 'alias':
        small['securities']['A']['universe_id'] = 'US:SAME'
        second['securities']['B']['universe_id'] = 'US:SAME'
    elif case == 'provider':
        second['source'] = {'provider': 'Yahoo/chart'}
    elif case == 'disjoint':
        small['sessions'], small['bars'] = small['sessions'][:2], small['bars'][:2]
        second['sessions'], second['bars'] = second['sessions'][3:], second['bars'][3:]
    elif case == 'empty':
        second['sessions'], second['bars'] = [], []
    ids = pair(store, small, second)
    with pytest.raises(ValueError, match=reason):
        compose_snapshots(store, ids)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM datasets').fetchone()[0] == 2


@pytest.mark.parametrize('snapshots', [[], 'not-a-list', [123], ['a', 'a'], ['a'] * 201])
def test_snapshot_selection_shape_is_validated(tmp_path, snapshots):
    with pytest.raises(ValueError):
        compose_snapshots(Store(tmp_path), snapshots)


def test_calendar_union_does_not_hide_missing_quotes(tmp_path, small, config):
    store = Store(tmp_path)
    second = other_symbol(small)
    missing = second['sessions'].pop(2)
    second['bars'] = [b for b in second['bars'] if b['date'] != missing]
    combined = compose_snapshots(store, pair(store, small, second))
    manifest, bars = store.load(combined)
    assert missing in manifest['sessions']
    with pytest.raises(ValueError, match='B/.*缺少行情'):
        preflight(manifest, bars, replace(config, symbols=['A', 'B']))


def test_common_interval_and_benchmark_are_enforced(tmp_path, small, config):
    store = Store(tmp_path)
    second = other_symbol(small)
    second['sessions'], second['bars'] = second['sessions'][1:], second['bars'][1:]
    ids = pair(store, small, second)
    with pytest.raises(ValueError, match='共同覆盖区间'):
        create_run(store, {'snapshots': ids, 'config': asdict(config)})
    good = replace(config, start=second['sessions'][0], symbols=['A', 'B'])
    combined = compose_snapshots(store, ids)
    manifest, bars = store.load(combined)
    assert preflight(manifest, bars, good) == second['sessions']
    with pytest.raises(ValueError, match='共同覆盖区间'):
        preflight(manifest, bars, config)
    with pytest.raises(ValueError, match='证券或基准'):
        create_run(store, {'snapshots': ids, 'config': asdict(replace(good, benchmark='unknown'))})


def test_composition_does_not_make_unverified_vwap_executable(tmp_path, small, config):
    store = Store(tmp_path)
    second = other_symbol(small)
    second['bars'][1]['quality_status'] = 'unverified'
    manifest, bars = store.load(compose_snapshots(store, pair(store, small, second)))
    with pytest.raises(ValueError, match='严格 VWAP'):
        preflight(manifest, bars, replace(config, symbols=['A', 'B']))


def test_reference_composition_keeps_price_basis_guards(tmp_path, small, config):
    store = Store(tmp_path)
    second = other_symbol(small)
    for data in (small, second):
        data['synthetic'] = False
        data['source'] = {'provider': 'Yahoo/chart'}
        for sec in data['securities'].values():
            sec.update(execution_blocked=YAHOO_BLOCK, actions_verified=False)
        for bar in data['bars']:
            bar.update(source='Yahoo/chart', price_basis=REFERENCE_BASIS, vwap_value=None, quality_status='unverified')
    manifest, bars = store.load(compose_snapshots(store, pair(store, small, second)))
    combined_config = replace(config, symbols=['A', 'B'], mode='reference_research')
    assert reference_available(manifest, combined_config.symbols)
    preflight(manifest, bars, combined_config)
    bars[0]['price_basis'] = 'unknown'
    with pytest.raises(ValueError, match='口径混合或未知'):
        preflight(manifest, bars, combined_config)


@pytest.mark.parametrize('kind', ['single', 'rolling', 'holdout'])
def test_multi_source_one_account_freezes_and_replays(tmp_path, small, config, kind):
    store = Store(tmp_path)
    ids = pair(store, small)
    code = '''def on_session(ctx):
    if not ctx.state.get("ordered"):
        for symbol in ctx.symbols:
            ctx.order_target_weight(symbol, "0.4")
        ctx.state["ordered"] = True
'''
    (tmp_path / 'user_strategies/portfolio.py').write_text(code, encoding='utf-8')
    combined_config = replace(config, symbols=['A', 'B'], benchmark='B')
    request = {'snapshots': ids, 'config': asdict(combined_config), 'strategy': 'custom', 'strategy_file': 'portfolio.py', 'kind': kind}
    if kind == 'rolling':
        request['research'] = {'interval': 'day', 'horizon': 3}
    elif kind == 'holdout':
        request['research'] = {'test_start': small['sessions'][2]}
    run_id = create_run(store, request)
    result = execute_run(store, run_id)
    archived = json.loads((tmp_path / 'runs' / run_id / 'request.json').read_text(encoding='utf-8'))
    assert set(archived['snapshots']) == set(ids)
    assert {s['snapshot'] for s in archived['data_sources']} == set(ids)
    assert archived['config']['initial_cash'] == '1000'
    sections = ([result] if kind == 'single' else [s['result'] for s in result['samples'] if s['status'] == 'completed'] if kind == 'rolling' else [result[k]['result'] for k in ('development', 'holdout')])
    for section in sections:
        assert {t['symbol'] for t in section['trades']} == {'A', 'B'}
        assert float(section['metrics']['final_equity']) == 1000
    # Updating a parent's head cannot change this run's frozen inputs.
    changed = deepcopy(small)
    changed['bars'][0]['close'] = '11'
    store.ingest(**changed)
    assert reproduce(store, run_id)['identical']


def test_api_accepts_multi_selection_and_keeps_legacy_single(tmp_path, small, config, monkeypatch):
    app = create_app(tmp_path)
    store, manager = app.state.store, app.state.manager
    ids = pair(store, small)
    started = []
    monkeypatch.setattr(manager, 'start', started.append)
    with TestClient(app) as client:
        headers = {'X-Lab-Request': 'local-ui'}
        assert 'multi_snapshot' in client.get('/api/status').json()['features']
        response = client.post('/api/runs', headers=headers, json={'snapshots': ids, 'config': asdict(replace(config, symbols=['A', 'B']))})
        assert response.status_code == 200
        detail = client.get('/api/runs/' + response.json()['run_id']).json()
        assert len(detail['request']['data_sources']) == 2
        assert len(started) == 1
        assert client.post('/api/runs', headers=headers, json={'snapshot': ids[0], 'config': asdict(config)}).status_code == 200
        assert client.post('/api/runs', headers=headers, json={'snapshots': ids, 'snapshot': ids[0], 'config': asdict(config)}).status_code == 400
        assert client.post('/api/runs', headers=headers, json={'snapshots': [], 'config': asdict(config)}).status_code == 400
