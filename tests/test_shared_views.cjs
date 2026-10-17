// Shared interface modules used by both the local app and the browser edition.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const UI = require('../src/investment_lab/web/static/ui-utils.js');
const Selection = require('../src/investment_lab/web/static/snapshot-selection.js');
const Charts = require('../src/investment_lab/web/static/charts.js');
const Results = require('../src/investment_lab/web/static/result-views.js');
const Editor = require('../src/investment_lab/web/static/strategy-editor.js');

const security = (symbol, extra = {}) => ({name: symbol, kind: 'stock', market: 'US', currency: 'USD', timezone: 'America/New_York', ...extra});
const snapshot = (id, symbols, extra = {}) => ({id, name: id, synthetic: false, first: '2024-01-02', last: '2024-03-29', provider: 'licensed',
  securities: Object.fromEntries(symbols.map(symbol => [symbol, security(symbol)])), ...extra});

test('selection keeps missing snapshots blocking instead of dropping them (H-09)', () => {
  const available = [snapshot('a', ['A'])];
  const result = Selection.evaluate({selectedIds: ['a', 'gone'], available});
  assert.deepEqual(result.missing, ['gone']);
  assert.equal(result.error, Selection.MESSAGES.missing);
  assert.equal(result.dataError, Selection.MESSAGES.missing, 'dates and symbols are not prepared from a partial selection');
  assert.deepEqual(Object.keys(result.securities), ['A'], 'symbols already known stay visible for correction');
  const custom = Selection.evaluate({selectedIds: ['gone'], available, messages: {missing: '浏览器缺少快照'}});
  assert.equal(custom.error, '浏览器缺少快照');
  assert.equal(Selection.evaluate({selectedIds: [], available}).error, Selection.MESSAGES.empty);
});

test('selection rejects mixed data, scopes, duplicates, providers and empty overlaps', () => {
  const cases = [
    [[snapshot('a', ['A']), snapshot('b', ['B'], {synthetic: true})], Selection.MESSAGES.synthetic],
    [[snapshot('a', ['A']), {...snapshot('b', []), securities: {B: security('B', {currency: 'HKD', market: 'HK'})}}], Selection.MESSAGES.scope],
    [[snapshot('a', ['A']), snapshot('b', ['A'])], /重复标的 A/],
    [[snapshot('a', ['A']), snapshot('b', ['B'], {provider: 'Yahoo/chart'})], Selection.MESSAGES.reference],
    [[snapshot('a', ['A'], {last: '2024-01-31'}), snapshot('b', ['B'], {first: '2024-02-01'})], Selection.MESSAGES.overlap],
    [[snapshot('a', ['A'], {first: null})], Selection.MESSAGES.calendar],
  ];
  for (const [available, expected] of cases) {
    const error = Selection.evaluate({selectedIds: available.map(item => item.id), available}).error;
    if (expected instanceof RegExp) assert.match(error, expected); else assert.equal(error, expected);
  }
  const ok = Selection.evaluate({selectedIds: ['a', 'b'], available: [snapshot('a', ['A'], {first: '2024-01-10'}), snapshot('b', ['B'], {last: '2024-02-15'})]});
  assert.equal(ok.error, '');
  assert.deepEqual(ok.range, {first: '2024-01-10', last: '2024-02-15'});
  const old = Selection.evaluate({selectedIds: ['a', 'b'], available: [snapshot('a', ['A']), snapshot('b', ['B'])], supportsMulti: false});
  assert.equal(old.error, Selection.MESSAGES.oldBackend);
  assert.equal(Selection.evaluate({selectedIds: ['a'], available: [snapshot('a', ['A'])], formError: '表单错误'}).error, '表单错误');
});

test('selection helpers keep index series out of trading and preserve symbol order', () => {
  const securities = {SPX: security('SPX', {kind: 'index'}), B: security('B'), A: security('A')};
  assert.deepEqual(Selection.tradableSymbols(securities), ['B', 'A']);
  assert.deepEqual(Selection.orderSymbols(['B', 'A'], ['A', 'B', 'C'], ['A', 'B', 'C']), ['B', 'A', 'C']);
  const record = Selection.fromRecord({id: 'r', name: 'n', first: '2024-01-02', last: '2024-01-05', rows: 4, source: {provider: 'Yahoo/chart'},
    catalog: {securities: {A: security('A')}, sessions: ['2024-01-02', '2024-01-05']}});
  assert.deepEqual(record.sessions, ['2024-01-02', '2024-01-05']);
  assert.equal(record.provider, 'Yahoo/chart');
  assert.equal(Selection.prefersReference([record], record.securities), true);
  assert.equal(Selection.prefersReference([record], {SPX: security('SPX', {kind: 'index'})}), false);
});

test('chart scales use readable ticks and dates align without inventing values', () => {
  const scale = Charts.niceTicks(0.93, 1.27, 5);
  assert.ok(scale.min <= 0.93 && scale.max >= 1.27);
  assert.ok(scale.ticks.every((tick, i) => i === 0 || tick > scale.ticks[i - 1]));
  assert.deepEqual(Charts.niceTicks(5, 5).ticks.length > 1, true, 'a flat series still gets a visible range');
  assert.equal(Charts.compactNumber(123456), '12.3万');
  assert.equal(Charts.compactNumber(2.5e8), '2.5亿');
  assert.equal(Charts.compactNumber(0.125), '0.13');
  const aligned = Charts.alignByDate([
    {label: 'a', points: [{date: '2024-01-03', value: 1}, {date: '2024-01-02', value: 2}]},
    {label: 'b', points: [{date: '2024-01-04', value: 3}]},
  ]);
  assert.deepEqual(aligned.dates, ['2024-01-02', '2024-01-03', '2024-01-04']);
  assert.deepEqual(aligned.series.map(item => item.values), [[2, 1, null], [null, null, 3]]);
});

test('axis labels stay distinct on very narrow ranges', () => {
  assert.equal(UI.stepDecimals(0.25), 2);
  assert.equal(UI.stepDecimals(2e-5), 5);
  assert.equal(UI.stepDecimals(1e-7), 7);
  assert.equal(UI.stepDecimals(5), 0);
  const distinct = options => {
    const labels = Charts.layout(options, 640).yTicks.map(tick => tick.label);
    assert.equal(new Set(labels).size, labels.length, labels.join(' | '));
    return labels;
  };
  const dates = ['2024-01-04', '2024-01-05', '2024-01-08'];
  const twr = Results.curveChart({curve: [{date: dates[0], twr_index: 1}, {date: dates[1], twr_index: 0.99991}, {date: dates[2], twr_index: 0.99991}]}, 'twr_index');
  assert.match(distinct(twr).join(' '), /0\.9999/);
  distinct(Results.curveChart({curve: [{date: dates[0], drawdown: 0}, {date: dates[1], drawdown: -0.00009}, {date: dates[2], drawdown: -0.00009}]}, 'drawdown'));
  const equity = distinct(Results.curveChart({curve: [{date: dates[0], equity: 100000}, {date: dates[1], equity: 99992}, {date: dates[2], equity: 99995}]}, 'equity'));
  assert.ok(equity.some(label => /99,99\d/.test(label)), 'a narrow range on a large amount is shown in full');
  assert.equal(Charts.compactNumber(1234567, 500000), '123万');
});

test('chart markup escapes labels and stays empty without finite data', () => {
  const options = {dates: ['2024-01-02', '2024-01-03'], series: [{label: '<b>x</b>', values: [1, 1.1], area: true}], baseline: 1, ariaLabel: '"><script>'};
  const svg = Charts.svgMarkup(options, 640);
  assert.match(svg, /^<svg /);
  assert.doesNotMatch(svg, /<script>/);
  assert.match(svg, /class="area s0"/);
  assert.match(Charts.legendMarkup(options.series), /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(Charts.tooltipMarkup(options, 1), /2024-01-03/);
  assert.equal(Charts.svgMarkup({dates: ['2024-01-02'], series: [{label: 'x', values: [null]}]}, 640), '');
});

test('result tables escape values and derive the trade side from the signed quantity', () => {
  const html = Results.table([{date: '2024-01-03', symbol: '<A>', quantity: '-5', price: '10', fee: '0', reason: '<img src=x>'}], Results.TABLE_COLUMNS.trades);
  assert.match(html, /卖出/);
  assert.match(html, /&lt;A&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.equal(Results.table([], Results.TABLE_COLUMNS.trades), '<p class="help">没有记录。</p>');
  const metrics = Results.metrics({total_return: 0.1, max_drawdown: -0.2, final_equity: 1000}, {currency: 'USD'});
  assert.match(metrics, /时间加权收益/);
  assert.doesNotMatch(metrics, /胜率/, 'optional statistics appear only when the engine produced them');
  assert.match(Results.metrics({win_rate: 0.5}), /胜率/);
});

test('curve charts overlay the benchmark only on the time-weighted index', () => {
  const result = {curve: [{date: '2024-01-02', twr_index: 1, drawdown: 0, equity: 1000}, {date: '2024-01-03', twr_index: 1.1, drawdown: -0.01, equity: 1100}],
    benchmark: {curve: [{date: '2024-01-02', twr_index: 1}, {date: '2024-01-03', twr_index: 1.05}]}, metadata: {currency: 'USD'}};
  assert.equal(Results.curveChart(result, 'twr_index').series.length, 2);
  assert.equal(Results.curveChart(result, 'twr_index', {benchmark: false}).series.length, 1);
  const drawdown = Results.curveChart(result, 'drawdown', {height: 120});
  assert.equal(drawdown.series.length, 1);
  assert.equal(drawdown.baseline, 0);
  assert.equal(drawdown.height, 120);
  assert.equal(Results.curveChart(result, 'equity').format(1100), 'USD 1,100');
});

test('comparison ranks real runs only and explains what differs', () => {
  const item = (id, extra = {}) => ({id, title: `<${id}>`, config: {start: '2024-01-02', end: '2024-03-29', mode: 'strict_vwap'},
    metrics: {total_return: 0.1, max_drawdown: -0.2}, currency: 'USD', curve: [{date: '2024-01-02', twr_index: 1}], ...extra});
  const ranked = Results.comparison([item('a'), item('b', {metrics: {total_return: 0.3, max_drawdown: -0.05}})]);
  assert.equal((ranked.table.match(/ best/g) || []).length, 2);
  assert.match(ranked.table, /data-open-run="a"/);
  assert.doesNotMatch(ranked.table, /<a>/);
  assert.equal(ranked.warnings, '');
  assert.equal(ranked.chart.series.length, 2);
  const mixed = Results.comparison([item('a', {synthetic: true}), item('b', {currency: 'HKD', config: {start: '2024-02-01', end: '2024-03-29'}})],
    {skipped: [{id: 'c0123456789', problem: '运行尚未完成'}], removable: true});
  assert.doesNotMatch(mixed.table, / best/);
  assert.match(mixed.warnings, /区间不同/);
  assert.match(mixed.warnings, /不同币种/);
  assert.match(mixed.warnings, /不标记最优/);
  assert.match(mixed.warnings, /c012345678 未列入：运行尚未完成/);
  assert.match(mixed.table, /data-uncompare="a"/);
  const linked = Results.comparison([item('a', {href: '#/runs/a'}), item('b', {href: '#/runs/b'})]);
  assert.match(linked.table, /<a href="#\/runs\/a">/);
});

test('CSV columns lead with the readable fields and cells resist spreadsheet formulas', () => {
  const rows = [{visible: {A: 1}, reason: 'x', symbol: 'A', date: '2024-01-03', quantity: '5'}, {extra: 1}];
  assert.deepEqual(Results.csvColumns(rows, 'trades'), ['date', 'symbol', 'quantity', 'reason', 'visible', 'extra']);
  assert.equal(UI.csvCell('=1+2'), "'=1+2");
  assert.equal(UI.csvCell('@SUM(A1)'), "'@SUM(A1)");
  assert.equal(UI.csvCell('-5.5'), '-5.5');
  assert.equal(UI.csvCell('-x'), "'-x");
  assert.equal(UI.csvCell('a,"b"'), '"a,""b"""');
  assert.equal(UI.csvCell({A: 1}), '"{""A"":1}"');
  assert.equal(UI.csvCell(null), '');
  assert.equal(UI.toCsv([{a: 1, b: 2}, {b: 3, c: 4}]), 'a,b,c\r\n1,2,\r\n,3,4\r\n');
});

test('run seals are deterministic, colourable and contain only drawing rectangles', () => {
  assert.equal(UI.seal('run-1'), UI.seal('run-1'));
  assert.notEqual(UI.seal('run-1'), UI.seal('run-2'));
  assert.match(UI.seal('run-1', 'small', 8), /class="seal h2 small"/);
  assert.doesNotMatch(UI.seal('<script>'), /<script>/);
  assert.deepEqual([...new Set(UI.seal('x').match(/<(\w+)/g))].sort(), ['<rect', '<span', '<svg'].sort());
});

test('preferences fall back safely when storage is missing, blocked or damaged', () => {
  assert.deepEqual(UI.readPreferences(null), {theme: 'auto', gain: 'red'});
  assert.deepEqual(UI.readPreferences({getItem: () => '{"theme":"neon","gain":"green"}'}), {theme: 'auto', gain: 'green'});
  assert.deepEqual(UI.readPreferences({getItem: () => '{broken'}), {theme: 'auto', gain: 'red'});
  assert.equal(UI.savePreferences({setItem() { throw new Error('quota'); }}, {theme: 'dark', gain: 'red'}), false);
  const blocked = {get localStorage() { throw new Error('SecurityError'); }};
  assert.equal(UI.safeStorage(blocked), null);
  const attributes = new Map();
  const doc = {documentElement: {setAttribute: (name, value) => attributes.set(name, value), removeAttribute: name => attributes.delete(name)}};
  UI.applyPreferences({theme: 'dark', gain: 'green'}, doc);
  assert.deepEqual(Object.fromEntries(attributes), {'data-theme': 'dark', 'data-gain': 'green'});
  UI.applyPreferences({theme: 'auto', gain: 'red'}, doc);
  assert.equal(attributes.size, 0);
});

test('strategy editor keeps unfinished JSON per strategy and never stores the placeholder', () => {
  const catalog = [{id: 'buy_hold', name: '买入持有', scope: 'single', params: []}, {id: 'dca', name: '定投', scope: 'single', params: []}];
  const element = value => ({value, textContent: '', innerHTML: '', classList: {toggle() {}}, addEventListener() {}, querySelectorAll: () => []});
  const strategy = element(''), params = element('{"weight":'), fields = element(''), status = element('');
  const editor = Editor.create({catalog: () => catalog, elements: {strategy, params, fields, status}, symbols: () => ['A']});
  editor.render(false);
  assert.deepEqual(editor.snapshot(), {}, 'the empty placeholder strategy never owns a draft');
  strategy.value = 'buy_hold';
  editor.resetActive();
  editor.render(false);
  assert.match(status.textContent, /JSON/);
  strategy.value = 'dca';
  editor.render(true);
  assert.equal(params.value, '{}', 'a strategy without a draft starts from its defaults');
  params.value = '{"amount": 1';
  strategy.value = 'buy_hold';
  editor.render(true);
  assert.equal(params.value, '{"weight":');
  assert.deepEqual(editor.snapshot(), {buy_hold: '{"weight":', dca: '{"amount": 1'});
  params.value = ' {} ';
  editor.restore({dca: 'x', '': 'placeholder'});
  assert.deepEqual(editor.snapshot(), {dca: 'x', buy_hold: ' {} '}, 'the visible editor text stays authoritative');
});
