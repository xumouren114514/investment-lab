/* 策略研究页：数据快照与标的、策略参数、草稿和命名配置、提交运行、最近结果、从历史运行载回配置。 */
const Selection = InvestmentLabSelection;

let datasets = [];
let selectedSnapshotIds = [];
let strategyCatalog = [];
let customStrategies = [];
let symbolOrder = [];
let selectedSymbols = [];
let knownSymbols = new Set();
let currentSecurities = {};
let configReady = false, restoringConfig = false;

const strategyEditor = InvestmentLabStrategyEditor.create({
  catalog: () => strategyCatalog,
  elements: {strategy: $('strategy'), params: $('params'), fields: $('strategy-parameter-fields'),
    status: $('strategy-parameter-status'), count: $('strategy-count')},
  symbols: () => selectedStrategySymbols(),
  onDraft: () => saveConfigDraft(),
  onValidity: () => refreshConfigValidity(),
});

/* Snapshot selection */
const selectionRecords = () => datasets.map(Selection.fromDataset);
const selectedDatasets = () => selectedSnapshotIds.map(id => datasets.find(d => d.id === id)).filter(Boolean);

function unavailableFormError() {
  const strategyOption = $('strategy').selectedOptions?.[0];
  const benchmarkOption = $('benchmark').selectedOptions?.[0];
  const missingSymbol = selectedSymbols.some(symbol => !Object.hasOwn(currentSecurities, symbol));
  return strategyOption?.dataset?.unavailable || benchmarkOption?.dataset?.unavailable || missingSymbol
    ? '已保存的策略、标的或基准当前不可用，请检查并重新选择。' : '';
}

function evaluateSelection(checkForm = true) {
  return Selection.evaluate({selectedIds: selectedSnapshotIds, available: selectionRecords(), supportsMulti: supportsMultiSnapshot,
    formError: checkForm ? unavailableFormError() : ''});
}

function datasetSelectionError(checkForm = true) { return evaluateSelection(checkForm).error; }

async function loadDatasets() {
  datasets = await api('/datasets');
  datasets.sort((a, b) => Number(b.synthetic) - Number(a.synthetic));
  $('dataset-options').innerHTML = datasets.map(d => {
    const tag = d.synthetic ? '<span class="tag">合成演示</span>' : d.reference_research_available ? '<span class="tag">参考价格</span>' : '';
    return `<label class="dataset-option"><input type="checkbox" name="snapshots" value="${esc(d.id)}" ${selectedSnapshotIds.includes(d.id) ? 'checked' : ''}><span><strong>${esc(d.name)}${tag}</strong><small>${esc(d.first ?? '—')} 至 ${esc(d.last ?? '—')}，${num(d.rows, 0)} 行，${Object.keys(d.symbols || {}).length} 个标的</small></span></label>`;
  }).join('');
  setDatasets();
  filterSnapshotOptions();
}

function filterSnapshotOptions() {
  const query = $('dataset-search').value.trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll('#dataset-options .dataset-option').forEach(row => {
    const d = datasets.find(item => item.id === row.querySelector('input').value);
    const text = [d.name, ...Object.entries(d.symbols || {}).flatMap(([symbol, s]) => [symbol, s.name, s.market, s.currency])].join(' ').toLowerCase();
    row.hidden = !!query && !text.includes(query);
    if (!row.hidden) visible++;
  });
  $('dataset-empty').hidden = visible > 0 || !datasets.length;
  $('dataset-match-count').textContent = datasets.length ? `显示 ${visible} / ${datasets.length}` : '还没有数据快照。可在“数据覆盖”导入数据包，或用命令行 init --demo 建立合成演示。';
}

function setDatasets() {
  const evaluation = evaluateSelection(false);
  const securities = evaluation.securities;
  const available = Object.keys(securities);
  const previous = new Set(selectedSymbols);
  const chosen = available.filter(symbol => previous.has(symbol) || (!knownSymbols.has(symbol) && securities[symbol].kind !== 'index'));
  symbolOrder = [...symbolOrder.filter(symbol => available.includes(symbol)), ...available.filter(symbol => !symbolOrder.includes(symbol))];
  selectedSymbols = symbolOrder.filter(symbol => chosen.includes(symbol));
  knownSymbols = new Set(available);
  currentSecurities = securities;
  renderSymbols();

  const previousBenchmark = $('benchmark').value;
  $('benchmark').innerHTML = '<option value="">不设置</option>' + Object.entries(securities).filter(([, s]) => s.kind !== 'index')
    .map(([symbol, s]) => `<option value="${esc(symbol)}">${esc(s.name || symbol)}（${esc(symbol)}）</option>`).join('');
  if (securities[previousBenchmark]) $('benchmark').value = previousBenchmark;

  $('dataset-count').textContent = `已选 ${selectedSnapshotIds.length} 个快照，${available.length} 个标的`;
  $('dataset-selected').innerHTML = selectedSnapshotIds.map(id => {
    const d = datasets.find(item => item.id === id), name = d?.name || `当前列表缺少快照 ${id.slice(0, 12)}`;
    return `<button type="button" class="chip${d ? ' on' : ' missing'}" data-remove-snapshot="${esc(id)}" aria-label="取消选择 ${esc(name)}">${esc(name)} <span aria-hidden="true">×</span></button>`;
  }).join('');
  $('dataset-clear').disabled = !selectedSnapshotIds.length;

  if (!evaluation.dataError && evaluation.range) {
    const {first, last} = evaluation.range;
    for (const id of ['start', 'end']) { $(id).min = first; $(id).max = last; }
    if (!$('start').value || $('start').value < first || $('start').value > last) $('start').value = first;
    if (!$('end').value || $('end').value > last || $('end').value < first) $('end').value = last;
    if ($('start').value >= $('end').value) { $('start').value = first; $('end').value = last; }
    $('dataset-range').textContent = `共同覆盖 ${first} 至 ${last}。缺失的行情仍会在运行时报告。`;
    if (Selection.prefersReference(evaluation.selected, securities)) $('mode').value = 'reference_research';
    else if ($('mode').value === 'reference_research') $('mode').value = 'strict_vwap';
  } else {
    $('dataset-range').textContent = '';
  }
  updatePriceModeNote();
  updateStrategyScopeNote();
  renderStrategyParameters(false);
  refreshConfigValidity();
}

$('dataset-options').addEventListener('change', event => {
  const box = event.target;
  if (!box.matches?.('input[name="snapshots"]') && box.name !== 'snapshots') return;
  if (box.checked) selectedSnapshotIds.push(box.value);
  else selectedSnapshotIds = selectedSnapshotIds.filter(id => id !== box.value);
  setDatasets();
});
$('dataset-search').addEventListener('input', filterSnapshotOptions);
$('dataset-search').addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
$('dataset-clear').addEventListener('click', () => {
  selectedSnapshotIds = [];
  document.querySelectorAll('#dataset-options input').forEach(box => { box.checked = false; });
  setDatasets();
  saveConfigDraft();
});
$('dataset-selected').addEventListener('click', event => {
  const button = event.target.closest?.('[data-remove-snapshot]');
  if (!button) return;
  const id = button.dataset.removeSnapshot;
  selectedSnapshotIds = selectedSnapshotIds.filter(item => item !== id);
  document.querySelectorAll('#dataset-options input').forEach(box => { if (box.value === id) box.checked = false; });
  setDatasets();
  saveConfigDraft();
});

/* Symbols: request order is the display order of the chosen chips. */
function chosenSymbols() { return symbolOrder.filter(symbol => selectedSymbols.includes(symbol)); }
function selectedStrategySymbols() { return chosenSymbols(); }

function renderSymbols() {
  const chosen = chosenSymbols();
  const host = $('symbols');
  if (!symbolOrder.length) {
    host.innerHTML = '<span class="help">先勾选数据快照。</span>';
    $('symbol-count').textContent = '';
    return;
  }
  host.innerHTML = symbolOrder.map(symbol => {
    const security = currentSecurities[symbol];
    const on = selectedSymbols.includes(symbol), first = chosen[0] === symbol;
    const label = security ? `${security.name || symbol}（${symbol}）` : `当前不可用 · ${symbol}`;
    const classes = ['chip', 'symbol-chip', on ? 'on' : '', security ? '' : 'missing', security?.kind === 'index' ? 'index' : ''].filter(Boolean).join(' ');
    return `<span class="${classes}"><label><input type="checkbox" data-symbol="${esc(symbol)}" ${on ? 'checked' : ''}>${esc(label)}${security?.kind === 'index' ? '，指数' : ''}</label>`
      + (first && chosen.length > 1 ? '<span class="first-tag">首项</span>' : on && !first ? `<button type="button" data-first-symbol="${esc(symbol)}" title="把它排在第一位">设为首项</button>` : '')
      + '</span>';
  }).join('');
  $('symbol-count').textContent = `已选 ${chosen.length} / ${Object.keys(currentSecurities).length}`;
}

function symbolsChanged() {
  renderSymbols();
  renderStrategyParameters(false);
  updateStrategyScopeNote();
  refreshConfigValidity();
  saveConfigDraft();
}

$('symbols').addEventListener('change', event => {
  const symbol = event.target.dataset?.symbol;
  if (!symbol) return;
  selectedSymbols = event.target.checked ? [...new Set([...selectedSymbols, symbol])] : selectedSymbols.filter(item => item !== symbol);
  symbolsChanged();
});
$('symbols').addEventListener('click', event => {
  const button = event.target.closest?.('[data-first-symbol]');
  if (!button) return;
  const symbol = button.dataset.firstSymbol;
  symbolOrder = [symbol, ...symbolOrder.filter(item => item !== symbol)];
  symbolsChanged();
});

function updateStrategyScopeNote() {
  const symbols = chosenSymbols(), entry = strategyEditor.entry();
  const first = currentSecurities[symbols[0]]?.name || symbols[0];
  $('strategy-scope-note').textContent = symbols.length > 1 && entry?.scope === 'single'
    ? `此预设只交易首项：${first}（${symbols[0]}）。需要多标的策略时，请选择组合策略。`
    : '所选标的共用同一账户和初始资金，实际买卖由策略决定。';
}

function updatePriceModeNote() {
  const evaluation = evaluateSelection(), selected = selectedDatasets(), d = selected[0];
  const note = $('dataset-note');
  if (evaluation.error) {
    note.textContent = evaluation.error;
    note.className = 'callout' + (selected.length || evaluation.missing.length ? ' invalid' : '');
    $('price-mode-note').textContent = '';
    return;
  }
  const reference = $('mode').value === 'reference_research';
  const available = selected.some(item => item.reference_research_available) && selected.every(item => item.reference_research_available ||
    (item.source?.provider === 'Yahoo/chart' && Object.values(item.symbols || {}).every(s => s.kind === 'index')));
  note.textContent = d.synthetic ? '合成演示数据，不是真实行情，只用于验证平台流程。'
    : reference ? (available ? '已选择参考历史价格研究。' + Results.REFERENCE_NOTE : '所选数据不支持参考历史价格模式，请选择其他价格模式。')
      : available ? '所选数据没有已核实的实际成交价。请选择“参考历史价格研究”运行；严格模式仍会阻止。'
        : '真实来源。先查看覆盖与 VWAP 口径；未验证的字段会阻止严格回测。';
  note.className = 'callout' + (d.synthetic || reference ? ' demo' : '');
  $('price-mode-note').textContent = reference ? Results.REFERENCE_NOTE : '下一交易日按所选价格模型模拟成交。';
  $('execution-price-label').textContent = reference ? '参考收盘价 + 滑点' : $('mode').value === 'close_research' ? '收盘价 + 滑点' : 'VWAP + 滑点';
}
$('mode').addEventListener('change', () => { updatePriceModeNote(); refreshConfigValidity(); });

/* Strategy parameters (shared editor) */
function renderStrategyParameters(changed = true) { strategyEditor.render(changed); }
function strategyParameterError() { return strategyEditor.error(); }
function currentStrategyParams() { return strategyEditor.value(); }

$('params').addEventListener('input', () => { renderStrategyParameters(false); refreshConfigValidity(); });
$('strategy').addEventListener('change', () => { renderStrategyParameters(true); updateStrategyScopeNote(); refreshConfigValidity(); });
$('benchmark').addEventListener('change', refreshConfigValidity);

/* Funding */
function syncMonthlyDeposit() {
  const input = $('monthly-deposit');
  try {
    if (document.activeElement !== input) input.value = InvestmentLabUI.readAmount($('flows').value, 'monthly', '资金流');
    $('funding-status').textContent = '入金只增加现金，不会自动买入；定投金额在策略参数中单独设置。';
    $('funding-status').classList.remove('run-error');
    input.disabled = false;
  } catch (error) {
    $('funding-status').textContent = error.message;
    input.disabled = true;
    $('funding-status').classList.add('run-error');
  }
}
$('monthly-deposit').addEventListener('input', () => {
  try {
    $('flows').value = InvestmentLabUI.writeAmount($('flows').value, 'monthly', $('monthly-deposit').value, '资金流');
    syncMonthlyDeposit();
    saveConfigDraft();
  } catch (error) {
    $('funding-status').textContent = error.message;
    $('funding-status').classList.add('run-error');
  }
});
$('flows').addEventListener('input', syncMonthlyDeposit);
$('monthly-example').addEventListener('click', safe(() => {
  const flows = JSON.parse($('flows').value || '{}');
  if (!flows || typeof flows !== 'object' || Array.isArray(flows)) throw new Error('入金/提款须为 JSON 对象');
  flows.monthly = 3000;
  $('flows').value = JSON.stringify(flows, null, 2);
  $('flows').focus();
  syncMonthlyDeposit();
  saveConfigDraft();
  notice('已填入每月入金 3000，原有日期金额保留；入金与策略买入金额分别设置。');
}));

function updateKindOptions() {
  $('rolling-options').hidden = $('kind').value !== 'rolling';
  $('holdout-options').hidden = $('kind').value !== 'holdout';
}
$('kind').addEventListener('change', updateKindOptions);

/* Validity and the run bar */
function configJsonError() {
  for (const [id, label] of [['flows', '资金流'], ['advanced', '其他引擎配置']]) {
    try { InvestmentLabStrategyForms.parseObject($(id).value, label); $(id).setCustomValidity(''); }
    catch (error) { $(id).setCustomValidity(error.message); return error.message; }
  }
  return '';
}

function refreshConfigValidity() {
  const blocker = datasetSelectionError() || strategyParameterError() || configJsonError();
  $('run-button').disabled = !!blocker;
  updatePriceModeNote();
  const hint = $('run-hint');
  if (blocker) {
    hint.textContent = '还不能运行：' + blocker;
    hint.className = 'run-hint blocked';
  } else {
    hint.textContent = `可以运行：${chosenSymbols().length} 个标的，${$('start').value} 至 ${$('end').value}。也可按 Ctrl+Enter。`;
    hint.className = 'run-hint ready';
  }
}

/* Drafts and named configurations (stored in this browser) */
function configRepository() { return ResearchConfig.repository(window.localStorage); }
function currentConfig() { return ResearchConfig.capture(document, selectedSnapshotIds, strategyEditor.snapshot(), chosenSymbols()); }

function saveConfigDraft() {
  if (!configReady || restoringConfig) return;
  try {
    configRepository().saveDraft(currentConfig());
    const jsonError = configJsonError();
    $('draft-status').textContent = '草稿已自动保存，' + new Date().toLocaleTimeString('zh-CN', {hour12: false}) + (jsonError ? '；' + jsonError : '');
    $('draft-status').classList.toggle('run-error', !!jsonError);
  } catch (error) {
    $('draft-status').textContent = '草稿未保存，请导出 JSON 备份。' + error.message;
    $('draft-status').classList.add('run-error');
  }
}

function refreshPresetList(selected = $('preset-list').value) {
  const names = configRepository().list();
  $('preset-list').replaceChildren(new Option(names.length ? '选择一份已保存的配置' : '暂无已保存配置', ''));
  for (const name of names) $('preset-list').add(new Option(name, name));
  if (names.includes(selected)) $('preset-list').value = selected;
  $('load-preset').disabled = !$('preset-list').value;
}

function restoreSelect(id, values) {
  const select = $(id);
  select.querySelectorAll('option[data-unavailable]').forEach(option => option.remove());
  for (const value of values) {
    if (![...select.options].some(option => option.value === value)) {
      const option = new Option('当前不可用 · ' + value, value);
      option.dataset.unavailable = 'true';
      select.add(option);
    }
  }
  for (const option of select.options) option.selected = values.includes(option.value);
}

function restoreSymbols(values) {
  symbolOrder = [...values, ...symbolOrder.filter(symbol => !values.includes(symbol))];
  selectedSymbols = [...values];
  renderSymbols();
}

function applyConfig(input) {
  const config = ResearchConfig.validate(input);
  // Check that every value is representable before changing any form field (e.g. malformed dates).
  for (const id of ResearchConfig.fields) {
    if (['strategy', 'benchmark'].includes(id)) continue;
    const copy = $(id).cloneNode(true);
    copy.value = config.fields[id];
    if (copy.value !== config.fields[id]) throw new Error(`配置字段 ${id} 无法载入，当前输入未替换。`);
  }
  restoringConfig = true;
  try {
    selectedSnapshotIds = [...config.snapshots];
    document.querySelectorAll('#dataset-options input').forEach(box => { box.checked = selectedSnapshotIds.includes(box.value); });
    setDatasets();
    // Restore after dataset defaults so that dates, price mode and symbol choices stay exact.
    for (const id of ResearchConfig.fields) {
      if (['strategy', 'benchmark'].includes(id)) restoreSelect(id, [config.fields[id]]);
      else $(id).value = config.fields[id];
    }
    // The visible editor text is authoritative, including whitespace and unfinished edits.
    strategyEditor.restore(config.strategyParams);
    restoreSymbols(config.symbols);
    renderStrategyParameters(false);
    syncMonthlyDeposit();
    $('preset-name').value = config.name;
    $('dataset-search').value = '';
    filterSnapshotOptions();
    updateKindOptions();
    updateStrategyScopeNote();
    refreshConfigValidity();
  } finally { restoringConfig = false; }
  return [datasetSelectionError(), strategyParameterError(), configJsonError()].filter(Boolean).join(' ');
}

function initializeConfigPersistence() {
  try {
    refreshPresetList();
    const draft = configRepository().draft();
    if (draft) {
      const warning = applyConfig(draft);
      $('draft-status').textContent = '已恢复上次草稿。';
      if (warning) notice('已恢复草稿；' + warning, true);
    } else {
      $('draft-status').textContent = '修改后自动保存草稿，刷新或重试可继续填写。';
    }
  } catch (error) {
    $('draft-status').textContent = '未能恢复本地配置；可以继续填写或导入 JSON。' + error.message;
    $('draft-status').classList.add('run-error');
  } finally {
    configReady = true;
    $('config-tools').disabled = false;
    refreshConfigValidity();
  }
}

$('run-form').addEventListener('input', event => {
  if (ResearchConfig.fields.includes(event.target.id) || event.target.id === 'preset-name') saveConfigDraft();
  if (['params', 'flows', 'advanced', 'start', 'end'].includes(event.target.id)) refreshConfigValidity();
});
$('run-form').addEventListener('change', event => {
  if (ResearchConfig.fields.includes(event.target.id) || event.target.name === 'snapshots') saveConfigDraft();
});
$('run-form').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    if (!$('run-button').disabled) $('run-form').requestSubmit();
  }
});
$('preset-name').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); $('save-preset').click(); } });
$('preset-list').addEventListener('change', () => { $('load-preset').disabled = !$('preset-list').value; });
$('save-preset').addEventListener('click', safe(() => {
  const name = $('preset-name').value.trim(), repo = configRepository();
  const exists = repo.list().includes(name);
  if (exists && !window.confirm(`覆盖已保存的配置“${name}”？`)) return;
  const saved = repo.save(name, currentConfig(), exists);
  $('preset-name').value = saved;
  refreshPresetList(saved);
  saveConfigDraft();
  notice(`配置“${saved}”已保存。`);
}));
$('load-preset').addEventListener('click', safe(() => {
  const name = $('preset-list').value;
  if (!name) return;
  const warning = applyConfig(configRepository().load(name));
  saveConfigDraft();
  notice(`已载入配置“${name}”。` + (warning ? ' ' + warning : ' 可以修改后运行。'), !!warning);
}));
$('export-config').addEventListener('click', safe(() => {
  $('config-json').value = ResearchConfig.encode(currentConfig());
  $('config-json').readOnly = true;
  $('apply-config-json').hidden = true;
  $('download-config-json').hidden = false;
  $('choose-config-file').hidden = true;
  $('config-dialog-title').textContent = '导出配置 JSON';
  $('config-dialog-status').textContent = '可下载文件，也可复制以下全文另存为 .json 文件。';
  $('config-dialog').showModal();
  $('config-json').select();
}));
$('download-config-json').addEventListener('click', safe(() => {
  const config = ResearchConfig.decode($('config-json').value);
  InvestmentLabUI.downloadBlob(new Blob([ResearchConfig.encode(config)], {type: 'application/json'}),
    InvestmentLabUI.safeFilename(config.name, '投资研究室配置') + '.json');
  $('config-dialog-status').textContent = '已请求下载；若应用窗口没有出现下载，请复制下方全文另存为 .json 文件。';
}));
function importConfigText(text) {
  const warning = applyConfig(ResearchConfig.decode(text));
  $('preset-list').value = '';
  $('load-preset').disabled = true;
  saveConfigDraft();
  $('config-dialog').close();
  notice('已导入配置；可填写名称后保存。' + (warning ? ' ' + warning : ''), !!warning);
}
$('import-config').addEventListener('click', () => {
  $('config-json').value = '';
  $('config-json').readOnly = false;
  $('apply-config-json').hidden = false;
  $('download-config-json').hidden = true;
  $('choose-config-file').hidden = false;
  $('config-dialog-title').textContent = '导入配置 JSON';
  $('config-dialog-status').textContent = '选择 JSON 文件或粘贴完整配置，然后载入。';
  $('config-dialog').showModal();
});
$('close-config-dialog').addEventListener('click', () => $('config-dialog').close());
$('apply-config-json').addEventListener('click', () => {
  try { importConfigText($('config-json').value); }
  catch (error) { $('config-dialog-status').textContent = error.message; }
});
$('choose-config-file').addEventListener('click', () => $('config-file').click());
$('config-file').addEventListener('change', safe(async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > ResearchConfig.maxBytes) throw new Error('配置文件不能超过 1 MB。');
    const text = await file.text();
    ResearchConfig.decode(text);
    $('config-json').value = text;
    $('config-dialog-status').textContent = '文件已读取，点击“载入 JSON”应用到表单。';
  } catch (error) {
    $('config-dialog-status').textContent = error.message;
  } finally { event.target.value = ''; }
}));
window.addEventListener('storage', event => {
  if (event.key?.startsWith('investment-lab.research-preset.v1:')) safe(() => refreshPresetList())();
});

/* Strategy catalogue */
async function loadStrategies() {
  const response = await api('/strategies'), old = $('strategy').value;
  strategyCatalog = response.catalog?.strategies || [];
  if (strategyCatalog.length !== 22) throw new Error('内置策略目录不完整；请更新或重启本机服务。');
  customStrategies = response.custom || [];
  const groups = new Map();
  for (const item of strategyCatalog) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  $('strategy').innerHTML = [...groups].map(([category, items]) => `<optgroup label="${esc(category)}">${items.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</optgroup>`).join('')
    + (customStrategies.length ? `<optgroup label="我的 Python 策略">${customStrategies.map(name => `<option value="custom:${esc(name)}">${esc(name)}</option>`).join('')}</optgroup>` : '');
  if (old) restoreSelect('strategy', [old]);
  else if (strategyCatalog.length) $('strategy').value = strategyCatalog[0].id;
  $('custom-files').innerHTML = '<option value="">新建策略</option>' + customStrategies.map(name => `<option>${esc(name)}</option>`).join('');
  strategyEditor.resetActive();
  renderStrategyParameters(false);
  syncMonthlyDeposit();
  updateStrategyScopeNote();
  if (configReady) refreshConfigValidity();
}

/* Submit */
$('run-form').addEventListener('submit', safe(async event => {
  event.preventDefault();
  saveConfigDraft();
  const selectionError = datasetSelectionError();
  if (selectionError) throw new Error(selectionError);
  const config = {start: $('start').value, end: $('end').value, symbols: chosenSymbols(), initial_cash: $('cash').value,
    max_leverage: $('leverage').value, mode: $('mode').value, commission_bps: $('commission').value, slippage_bps: $('slippage').value,
    annual_interest: $('interest').value, warmup: Number($('warmup').value), cash_flows: JSON.parse($('flows').value),
    benchmark: $('benchmark').value || null, ...JSON.parse($('advanced').value)};
  const chosen = $('strategy').value, params = currentStrategyParams();
  const request = {...(selectedSnapshotIds.length === 1 ? {snapshot: selectedSnapshotIds[0]} : {snapshots: [...selectedSnapshotIds]}),
    config, strategy: chosen.startsWith('custom:') ? 'custom' : chosen, params, kind: $('kind').value};
  if (chosen.startsWith('custom:')) request.strategy_file = chosen.slice(7);
  if (request.kind === 'rolling') request.research = {interval: $('interval').value, horizon: Number($('horizon').value), end_mode: $('end-mode').value};
  if (request.kind === 'holdout') request.research = {test_start: $('test-start').value, gap_sessions: Number($('gap').value)};
  $('run-button').disabled = true;
  try {
    const created = await api('/runs', request);
    notice('已提交，正在计算。可以继续浏览其他页面。');
    watchRun(created.run_id);
    go('#/runs/' + encodeURIComponent(created.run_id));
  } finally { refreshConfigValidity(); }
}));

/* Latest completed single run, shown beside the form */
async function loadLatestPreview() {
  const runs = await api('/runs?limit=30');
  const latest = runs.find(run => run.status === 'completed' && run.kind === 'single');
  if (!latest) return;
  renderPreview(latest.id, await api('/runs/' + encodeURIComponent(latest.id)));
}

function renderPreview(id, detail) {
  const result = detail.result, request = detail.request;
  if (!result?.metrics) return;
  $('latest-preview').innerHTML = `<div class="row spread"><div class="preview-title">${InvestmentLabUI.seal(id)}<div><h3>${esc(strategyName(request))}</h3><p>${esc(request.config.start)} 至 ${esc(request.config.end)}，最近一次单次回测</p></div></div>${Results.dataBadge({synthetic: request.synthetic, reference: isReference(request)})}</div>`
    + (isReference(request) ? `<p class="callout demo">${esc(Results.REFERENCE_NOTE)}</p>` : '')
    + Results.compactMetrics(result.metrics, {currency: result.metadata?.currency})
    + '<div class="chart" id="preview-chart"></div>'
    + `<div class="row"><a class="btn" href="#/runs/${encodeURIComponent(id)}">打开完整结果</a></div>`;
  InvestmentLabCharts.render($('preview-chart'), {...Results.curveChart(result, 'twr_index', {benchmark: false, height: 200}),
    legend: false, ariaLabel: '最近一次回测的时间加权净值'});
}

jobWatch.listeners.add(async (run, terminal) => {
  if (terminal && run.status === 'completed') await loadLatestPreview();
});

/* Load a past run's settings back into the form; engine settings equal to defaults are left out. */
const FORM_CONFIG_KEYS = new Set(['start', 'end', 'symbols', 'initial_cash', 'mode', 'commission_bps', 'slippage_bps', 'max_leverage',
  'annual_interest', 'warmup', 'cash_flows', 'benchmark']);

function configFromRun(request) {
  const config = request.config || {}, research = request.research || {}, current = currentConfig();
  const advanced = {};
  for (const [key, value] of Object.entries(config)) {
    if (FORM_CONFIG_KEYS.has(key)) continue;
    if (Object.hasOwn(configDefaults, key) && JSON.stringify(configDefaults[key]) === JSON.stringify(value)) continue;
    advanced[key] = value;
  }
  const strategy = request.strategy === 'custom' ? `custom:${request.strategy_file || request.strategy_entry || ''}` : request.strategy;
  const paramsText = JSON.stringify(request.params || {}, null, 2);
  const text = value => value === null || value === undefined ? '' : String(value);
  return ResearchConfig.validate({format: ResearchConfig.format, version: 1, name: '',
    snapshots: [...new Set(request.snapshots || [request.snapshot])], symbols: [...(config.symbols || [])],
    fields: {...current.fields, start: text(config.start), end: text(config.end), strategy, kind: request.kind || 'single',
      cash: text(config.initial_cash), leverage: text(config.max_leverage), mode: text(config.mode),
      interval: text(research.interval ?? current.fields.interval), horizon: text(research.horizon ?? current.fields.horizon),
      'end-mode': text(research.end_mode ?? current.fields['end-mode']), 'test-start': text(research.test_start ?? current.fields['test-start']),
      gap: text(research.gap_sessions ?? current.fields.gap), commission: text(config.commission_bps), slippage: text(config.slippage_bps),
      interest: text(config.annual_interest), warmup: text(config.warmup ?? 0), benchmark: text(config.benchmark),
      params: paramsText, flows: JSON.stringify(config.cash_flows || {}, null, 2), advanced: JSON.stringify(advanced, null, 2)},
    strategyParams: {...current.strategyParams, [strategy]: paramsText}});
}

function loadRunIntoForm(id, request) {
  const warning = applyConfig(configFromRun(request));
  saveConfigDraft();
  go('#/research');
  const custom = request.strategy === 'custom' ? ' Python 策略会使用当前文件内容，而不是该运行冻结的源码。' : '';
  notice(`已载入运行 ${id.slice(0, 10)} 的配置。${custom}${warning ? ' ' + warning : ' 可以修改后再运行。'}`, !!warning);
}

routeHandlers.research = () => { refreshConfigValidity(); };
