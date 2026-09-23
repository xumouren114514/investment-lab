const {test} = require('node:test');
const assert = require('node:assert/strict');
const C = require('../src/investment_lab/web/static/config-presets.js');

function storage() {
  const data = new Map();
  return {get length() { return data.size; }, key: i => [...data.keys()][i],
    getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value)};
}
function config() {
  return {format: C.format, version: 1, name: '定投配置', snapshots: ['frozen-b', 'frozen-a'],
    symbols: ['B', 'A'], fields: {...Object.fromEntries(C.fields.map(id => [id, ''])),
      strategy: 'custom:定投.py', kind: 'rolling', cash: '12345.67', mode: 'reference_research',
      interval: 'quarter', horizon: '45', 'end-mode': 'common_end', 'test-start': '2025-01-02',
      gap: '5', params: '{"unfinished":', flows: '{\n  "monthly": 3000, "2024-02-01": -100\n}',
      advanced: '{"max_order_fraction":"0.3"}'}};
}

test('reload, named load and file roundtrip retain exact raw text and snapshot order', () => {
  const disk = storage(), repo = C.repository(disk), saved = config();
  repo.save(saved.name, saved);
  repo.saveDraft(saved);
  const reloaded = C.repository(disk);
  assert.deepEqual(reloaded.draft(), saved);
  assert.deepEqual(reloaded.load(saved.name), saved);
  assert.deepEqual(C.decode(C.encode(saved)), saved);
  const edited = {...saved, fields: {...saved.fields, cash: '20'}};
  reloaded.saveDraft(edited);
  assert.equal(reloaded.draft().fields.cash, '20');
  assert.equal(reloaded.load(saved.name).fields.cash, '12345.67');
});

test('two tabs keep distinct presets, duplicate names require explicit overwrite', () => {
  const disk = storage(), first = C.repository(disk), second = C.repository(disk);
  first.save('__proto__', config());
  second.save('第二份', config());
  assert.deepEqual(new Set(first.list()), new Set(['__proto__', '第二份']));
  const updated = {...config(), fields: {...config().fields, kind: 'holdout'}};
  assert.throws(() => first.save('第二份', updated), /同名/);
  assert.equal(second.load('第二份').fields.kind, 'rolling');
  first.save('第二份', updated, true);
  assert.equal(second.load('第二份').fields.kind, 'holdout');
});

test('bad imports and damaged saved records fail without replacing existing content', () => {
  const disk = storage(), repo = C.repository(disk), saved = config();
  repo.saveDraft(saved);
  for (const raw of ['{', JSON.stringify({...saved, version: 2}),
    JSON.stringify({...saved, snapshots: ['same', 'same']}),
    JSON.stringify({...saved, fields: {cash: 1000}}), ' '.repeat(C.maxBytes + 1)]) {
    assert.throws(() => repo.saveDraft(C.decode(raw)));
    assert.deepEqual(repo.draft(), saved);
  }
  disk.setItem('investment-lab.research-preset.v1:broken', '{');
  assert.throws(() => repo.load('broken'), /损坏/);
  assert.deepEqual(repo.draft(), saved);
});

test('storage denial propagates and the current form still exports without storage', () => {
  const saved = config(), denied = C.repository({setItem() { throw new Error('QuotaExceededError'); }});
  assert.throws(() => denied.saveDraft(saved), /QuotaExceededError/);
  assert.deepEqual(C.decode(C.encode(saved)), saved);
  assert.deepEqual(C.validate({...saved, injected: '<script>'}), saved);
});
