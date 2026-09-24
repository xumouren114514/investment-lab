const {test} = require('node:test');
const assert = require('node:assert/strict');
const UI = require('../src/investment_lab/web/static/ui-utils.js');

test('guided monetary inputs preserve advanced JSON fields and validate schedule amounts', () => {
  const edited = UI.writeAmount('{"2024-02-01":-100}', 'monthly', '3000.50', '资金流');
  assert.equal(UI.readAmount(edited, 'monthly', '资金流'), '3000.50');
  assert.deepEqual(JSON.parse(edited), {"2024-02-01":-100, monthly:'3000.50'});
  assert.deepEqual(JSON.parse(UI.writeAmount(edited, 'monthly', '', '资金流')), {"2024-02-01":-100});
  assert.throws(() => UI.writeAmount('{}', 'monthly', '-5', '资金流'), /大于0/);
  assert.throws(() => UI.writeAmount('{}', 'monthly', '1.001', '资金流'), /最多两位小数/);
  assert.throws(() => UI.readAmount('[]', 'monthly', '资金流'), /必须是 JSON 对象/);
});

test('guided strategy options merge into parameters without dropping unrelated keys', () => {
  const edited = UI.writeChoice('{"window":20}', 'portfolio_mode', 'no_rebalance',
    ['rebalance', 'no_rebalance'], '策略参数');
  assert.equal(UI.readChoice(edited, 'portfolio_mode', 'rebalance',
    ['rebalance', 'no_rebalance'], '策略参数'), 'no_rebalance');
  assert.deepEqual(JSON.parse(edited), {window:20, portfolio_mode:'no_rebalance'});
  assert.throws(() => UI.readChoice('{"portfolio_mode":"other"}', 'portfolio_mode', 'rebalance',
    ['rebalance', 'no_rebalance'], '策略参数'), /不是可选值/);
});

test('snapshot selection reuses a compact catalog and upgrades older browser records once', () => {
  const record = {packageJson:JSON.stringify({manifest:{securities:{A:{name:'A'}},sessions:['2024-01-02']},bars:Array(20).fill({close:'10'})})};
  const first = UI.snapshotCatalog(record);
  assert.equal(first.migrated, true);
  assert.deepEqual(first.catalog, {securities:{A:{name:'A'}},sessions:['2024-01-02']});
  const second = UI.snapshotCatalog(record);
  assert.equal(second.migrated, false);
  assert.equal(second.catalog, first.catalog);
});

test('chart helpers retain endpoints and spikes while bounding large-history work', () => {
  const history = Array.from({length:250_000}, (_, index) => 100 + Math.sin(index / 150));
  history[91_237] = 500;
  history[170_001] = -20;
  const sampled = UI.minMaxSample(history, 1200);
  assert.ok(sampled.length <= 1200);
  assert.equal(sampled[0].index, 0);
  assert.equal(sampled.at(-1).index, history.length - 1);
  assert.ok(sampled.some(point => point.index === 91_237 && point.value === 500));
  assert.ok(sampled.some(point => point.index === 170_001 && point.value === -20));
  assert.deepEqual(UI.extent([history, [null, 'bad', 900]]), {min:-20, max:900});
  assert.equal(UI.extent([[null, 'bad']]), null);
});
