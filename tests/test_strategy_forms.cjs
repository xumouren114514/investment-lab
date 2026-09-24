const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Forms = require('../src/investment_lab/web/static/strategy-forms.js');

const catalogPath = path.join(__dirname, '..', 'src', 'investment_lab', 'strategies', 'catalog.json');
const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
const entry = id => catalog.strategies.find(item => item.id === id);

test('the shared catalogue exposes 22 strategies with valid editable defaults', () => {
  assert.equal(catalog.strategies.length, 22);
  assert.equal(new Set(catalog.strategies.map(item => item.id)).size, 22);
  for (const item of catalog.strategies) {
    const defaults = Forms.defaults(item);
    const checked = Forms.validateValues(item, defaults, ['A', 'B']);
    assert.equal(checked.error, null, `${item.id}: ${checked.error}`);
  }
});

test('strategy form validation enforces ranges and cross-field constraints', () => {
  assert.match(Forms.validateValues(entry('ema_crossover'), {fast_window:26, slow_window:26, weight:.95}).error, /快线窗口/);
  assert.match(Forms.validateValues(entry('macd'), {fast_window:1, slow_window:26, signal_window:9, weight:.95}).error, /不能小于/);
  assert.match(Forms.validateValues(entry('rotation'), {window:20, top_n:2, frequency:'monthly', weight:.95}, ['A']).error, /不能超过/);
  assert.match(Forms.validateValues(entry('rsi_mean_reversion'), {window:14, entry:60, exit:55, weight:.95}).error, /入场线/);
  assert.match(Forms.validateValues(entry('donchian_breakout'), {entry_window:10, exit_window:11, weight:.95}).error, /退出窗口/);
});

test('numeric input step hints never make a default parameter invalid in the browser', () => {
  const amount = entry('dca').parameters.find(field => field.key === 'amount');
  const weight = entry('buy_hold').parameters.find(field => field.key === 'weight');
  assert.equal(Forms.stepFor(amount), 'any');
  assert.equal(Forms.stepFor(weight), 0.05);
  assert.equal(Forms.stepFor({type:'integer'}), 1);
  assert.equal(Forms.stepFor({type:'select'}), 'any');
});

test('portfolio weights, strict dates and legacy extra parameters round-trip', () => {
  const allocation = entry('target_allocation');
  assert.match(Forms.validateValues(allocation, {weights:{A:.7, B:.4}, frequency:'monthly', rebalance_threshold:.05}, ['A','B']).error, /总和/);
  assert.match(Forms.validateValues(allocation, {weights:{C:.2}, frequency:'monthly', rebalance_threshold:.05}, ['A','B']).error, /未被选中/);
  const equal = Forms.validateValues(allocation, {weights:{A:.6, B:.4}, frequency:'monthly', rebalance_threshold:.05, legacy_flag:true}, ['A','B']);
  assert.equal(equal.error, null);
  assert.equal(equal.value.legacy_flag, true);
  assert.match(Forms.validateValues(entry('monthly_equal_weight'), {portfolio_mode:'rebalance', rebalance_threshold:.05, month_end_dates:['2024-02-30']}).error, /有效/);
  assert.match(Forms.validateValues(entry('monthly_equal_weight'), {portfolio_mode:'rebalance', rebalance_threshold:.05, month_end_dates:['2024-02-29','2024-02-29']}).error, /重复/);
});
