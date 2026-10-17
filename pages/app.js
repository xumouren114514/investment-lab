/* 浏览器版：快照存于 IndexedDB，草稿存于 localStorage，计算在 Web Worker（Pyodide）中完成。
   共享模块：ui-utils、strategy-forms、strategy-editor、snapshot-selection、result-views、charts。 */
const $ = selector => document.querySelector(selector);
const UI = window.InvestmentLabUI;
const Results = window.InvestmentLabResults;
const Charts = window.InvestmentLabCharts;
const Selection = window.InvestmentLabSelection;
const esc = UI.escapeHtml;
const DB_NAME = 'investment-lab-pages';
const DB_VERSION = 1;
const DRAFT_KEY = 'investment-lab-pages-draft-v1';
const FIELD_IDS = ['strategy','kind','start','end','cash','leverage','mode','benchmark','commission','minimum-commission','slippage','interest','params','flows','interval','horizon','end-mode','test-start','gap'];
const MISSING_MESSAGE = '已选择的部分快照不在此浏览器中；原编号仍保留。请重新导入对应数据包，或取消这些选择后再运行。不会自动改用其他快照。';
const pending = new Map();
let database;
let worker;
let packages = [];
let runs = [];
let selectedIds = [];
let selectedSymbols = [];
let symbolOrder = [];
// Symbols offered at the last complete selection: only symbols new to it are chosen automatically.
let knownSymbols = new Set();
// A restored symbol selection is applied exactly once all of its snapshots are present.
let symbolsPinned = false;
// A restored benchmark waits for its options; until its snapshot returns it stays marked and blocking.
let pendingBenchmark = null;
let strategyCatalog = [];
let runtimeManifest, runtimeManifestHash = '', strategySource = '';
let running = false;
let resultView = null;
const compareIds = new Set();
const summaryCache = new Map();

const strategyEditor = InvestmentLabStrategyEditor.create({
  catalog: () => strategyCatalog,
  elements: {strategy: $('#strategy'), params: $('#params'), fields: $('#strategy-parameter-fields'),
    status: $('#strategy-parameter-status'), count: $('#strategy-count')},
  symbols: () => selectedStrategySymbols(),
  onRender: () => updateStrategyScopeNote(),
  onDraft: () => saveDraft(),
  onValidity: () => refreshRunButton(),
});

async function sha256Hex(value) {
  const bytes = value instanceof Uint8Array ? value : new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function escapeHtml(value) { return esc(value); }

function notice(message, error = false) {
  const live = $('#notice');
  if (live) live.textContent = message;
  const host = $('#toasts');
  if (!host || typeof host.prepend !== 'function') return;
  const toast = document.createElement('div');
  toast.className = 'toast' + (error ? ' error' : '');
  toast.setAttribute('role', error ? 'alert' : 'status');
  toast.innerHTML = `<p>${esc(message)}</p><button type="button" aria-label="关闭提示">×</button>`;
  toast.querySelector('button').addEventListener('click', () => toast.remove());
  host.prepend(toast);
  while (host.children.length > 4) host.lastElementChild.remove();
  if (!error) setTimeout(() => toast.remove(), 8000);
}

/* Browser storage */
function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('snapshots')) db.createObjectStore('snapshots', {keyPath:'id'});
      if (!db.objectStoreNames.contains('runs')) db.createObjectStore('runs', {keyPath:'id'});
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('无法打开本机浏览器存储。'));
    request.onblocked = () => reject(new Error('另一个页面正在更新本机数据库，请关闭旧页面后重试。'));
  });
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('浏览器存储操作失败。'));
  });
}

async function listStore(name) { return requestResult(database.transaction(name, 'readonly').objectStore(name).getAll()); }
async function putStore(name, value) { return requestResult(database.transaction(name, 'readwrite').objectStore(name).put(value)); }
async function deleteStore(name, key) { return requestResult(database.transaction(name, 'readwrite').objectStore(name).delete(key)); }

/* Worker */
function getWorker() {
  if (worker) return worker;
  worker = new Worker(new URL('./worker.js?v=20261007-1', import.meta.url), {type:'module'});
  worker.onmessage = event => {
    let message;
    try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; }
    catch (_) { return; }
    const task = pending.get(message.requestId);
    if (!task) return;
    if (message.type === 'progress') {
      task.progress?.(message);
      return;
    }
    pending.delete(message.requestId);
    if (message.type === 'error') task.reject(new Error(message.error || '浏览器计算失败。'));
    else task.resolve(message);
  };
  worker.onerror = event => {
    const failure = new Error(`浏览器计算线程失败：${event.message || '未知错误'}`);
    for (const task of pending.values()) task.reject(failure);
    pending.clear();
    worker?.terminate();
    worker = null;
  };
  return worker;
}

function workerRequest(type, payload = {}, progress = null) {
  const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return new Promise((resolve, reject) => {
    pending.set(requestId, {resolve, reject, progress});
    try { getWorker().postMessage({type, requestId, ...payload}); }
    catch (error) { pending.delete(requestId); reject(error); }
  });
}

/* Drafts */
function draftValues() { return Object.fromEntries(FIELD_IDS.map(id => [id, $(`#${id}`).value])); }

function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({fields:draftValues(), selectedIds, selectedSymbols, strategyParams:strategyEditor.snapshot()}));
  } catch (_) { notice('浏览器无法保存自动草稿；请导出本机数据备份。', true); }
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    for (const [id, value] of Object.entries(draft.fields || {})) {
      if (FIELD_IDS.includes(id) && $(`#${id}`)) $(`#${id}`).value = value;
    }
    selectedIds = Array.isArray(draft.selectedIds) ? draft.selectedIds : [];
    selectedSymbols = Array.isArray(draft.selectedSymbols) ? draft.selectedSymbols : [];
    symbolOrder = [...selectedSymbols];
    symbolsPinned = true;
    // The benchmark options do not exist yet; the saved choice is applied once they do.
    pendingBenchmark = typeof draft.fields?.benchmark === 'string' ? draft.fields.benchmark : null;
    const saved = draft.strategyParams && typeof draft.strategyParams === 'object' && !Array.isArray(draft.strategyParams) ? draft.strategyParams : {};
    // Earlier browser drafts stored parsed objects. Accept both without parsing
    // the new raw strings, which may deliberately contain unfinished JSON.
    strategyEditor.restore(Object.fromEntries(Object.entries(saved).map(([id, value]) =>
      [id, typeof value === 'string' ? value : JSON.stringify(value)])));
  } catch (_) { /* A damaged draft falls back to safe form defaults. */ }
  strategyEditor.resetActive();
  renderStrategyParameters(false);
  syncMonthlyDeposit();
  updateStrategyScopeNote();
  updateKind();
}

/* Strategy parameters (shared editor) */
function renderStrategyParameters(changed = true) { strategyEditor.render(changed); }
function strategyParameterError() { return strategyEditor.error(); }
function selectedStrategySymbols() { return [...selectedSymbols]; }

function updateStrategyScopeNote() {
  const entry = strategyEditor.entry();
  $('#strategy-scope-note').textContent = selectedSymbols.length > 1 && entry?.scope === 'single'
    ? `此策略只交易首项：${selectedSymbols[0]}。需要同时配置多个标的时，请选择组合策略。`
    : '所选标的共用同一账户和初始资金；具体交易由所选策略决定。';
}

function syncMonthlyDeposit() {
  const input = $('#monthly-deposit');
  try {
    if (document.activeElement !== input) input.value = UI.readAmount($('#flows').value, 'monthly', '资金流');
    $('#funding-status').textContent = '入金只增加现金，不会自动买入；定投金额在策略参数中单独设置。';
    $('#funding-status').classList.remove('run-error');
    input.disabled = false;
  } catch (error) {
    $('#funding-status').textContent = error.message;
    $('#funding-status').classList.add('run-error');
    input.disabled = true;
  }
}

$('#strategy').addEventListener('change', () => renderStrategyParameters(true));
$('#monthly-deposit').addEventListener('input', () => {
  try { $('#flows').value = UI.writeAmount($('#flows').value, 'monthly', $('#monthly-deposit').value, '资金流'); syncMonthlyDeposit(); saveDraft(); }
  catch (error) { $('#funding-status').textContent = error.message; $('#funding-status').classList.add('run-error'); }
});
$('#flows').addEventListener('input', () => { syncMonthlyDeposit(); refreshRunButton(); });
$('#params').addEventListener('input', () => { renderStrategyParameters(false); refreshRunButton(); });

/* Snapshot selection (shared rules: missing, mixing, scope, overlap) */
function selectionRecords() {
  return packages.map(record => {
    const {migrated} = UI.snapshotCatalog(record);
    if (migrated && database) putStore('snapshots', record).catch(() => {});
    return Selection.fromRecord(record);
  });
}

function evaluateSelection() {
  return Selection.evaluate({selectedIds, available: selectionRecords(), messages: {missing: MISSING_MESSAGE}});
}

function runBlocker() {
  const evaluation = evaluateSelection();
  if (evaluation.error) return evaluation.error;
  if (!selectedSymbols.length) return '请至少勾选一个交易标的。';
  if ($('#benchmark').selectedOptions?.[0]?.dataset?.unavailable) return '已保存的基准当前不可用：请重新导入对应快照，或改选基准。';
  const parameterError = strategyParameterError();
  if (parameterError) return parameterError;
  try { InvestmentLabStrategyForms.parseObject($('#flows').value, '资金流'); }
  catch (error) { return error.message; }
  return '';
}

function refreshRunButton() {
  let flowError = '';
  try { InvestmentLabStrategyForms.parseObject($('#flows').value, '资金流'); } catch (error) { flowError = error.message; }
  $('#flows').setCustomValidity(flowError);
  const blocker = runBlocker();
  $('#run').disabled = !!blocker || running;
  const hint = $('#run-hint');
  if (running) { hint.textContent = '正在计算…'; hint.className = 'run-hint'; }
  else if (blocker) { hint.textContent = '还不能运行：' + blocker; hint.className = 'run-hint blocked'; }
  else { hint.textContent = `可以运行：${selectedSymbols.length} 个标的，${$('#start').value} 至 ${$('#end').value}。`; hint.className = 'run-hint ready'; }
  updateSelectionNote();
}

function updateSelectionNote() {
  const evaluation = evaluateSelection(), note = $('#selection-note');
  if (evaluation.error && (evaluation.selected.length || evaluation.missing.length)) {
    note.textContent = evaluation.error;
    note.className = 'callout invalid';
  } else if (evaluation.selected.length && evaluation.selected.every(item => item.synthetic)) {
    note.textContent = '合成演示数据，不是真实行情，只用于验证流程。';
    note.className = 'callout demo';
  } else if ($('#mode').value === 'reference_research' && evaluation.selected.length) {
    note.textContent = Results.REFERENCE_NOTE;
    note.className = 'callout demo';
  } else {
    note.textContent = '';
    note.className = 'callout';
  }
}

async function refreshStorage() {
  packages = await listStore('snapshots');
  runs = (await listStore('runs')).sort((a, b) => b.created.localeCompare(a.created));
  $('#storage-label').textContent = `此浏览器保存了 ${packages.length} 个快照、${runs.length} 次研究。`;
  $('#export-backup').disabled = packages.length === 0 && runs.length === 0;
  renderPackages();
  renderRuns();
  updateSymbols();
}

function renderPackages() {
  const host = $('#snapshot-list');
  if (!packages.length) {
    host.innerHTML = '<div class="empty"><p>还没有快照。用页首的“导入快照”，或先试用五日合成演示。</p></div>';
  } else {
    host.innerHTML = packages.map(item => `<div class="snapshot-card"><label><input type="checkbox" data-select-snapshot="${esc(item.id)}" ${selectedIds.includes(item.id) ? 'checked' : ''}><span><span class="snapshot-name">${esc(item.name)}</span><span class="snapshot-meta">${item.synthetic ? '合成演示' : '真实来源'}，${esc(item.first)} 至 ${esc(item.last)}，${Number(item.rows).toLocaleString()} 行，${esc(item.id.slice(0, 12))}</span></span></label><button type="button" class="small quiet" data-delete-snapshot="${esc(item.id)}">移除</button></div>`).join('');
  }
  renderMissing();
}

function renderMissing() {
  const missing = evaluateSelection().missing, host = $('#missing-snapshots');
  host.hidden = !missing.length;
  host.innerHTML = missing.map(id => `<button type="button" class="chip missing" data-remove-missing="${esc(id)}" aria-label="取消选择缺失的快照 ${esc(id.slice(0, 12))}">此浏览器缺少快照 ${esc(id.slice(0, 12))} <span aria-hidden="true">×</span></button>`).join('');
}

$('#snapshot-list').addEventListener('change', event => {
  const id = event.target.dataset?.selectSnapshot;
  if (!id) return;
  // Toggle only this snapshot: selections missing from this browser stay visible and blocking.
  selectedIds = event.target.checked ? [...new Set([...selectedIds, id])] : selectedIds.filter(item => item !== id);
  updateSymbols();
});
$('#snapshot-list').addEventListener('click', async event => {
  const button = event.target.closest?.('[data-delete-snapshot]');
  if (!button) return;
  const record = packages.find(item => item.id === button.dataset.deleteSnapshot);
  if (!window.confirm(`从此浏览器移除快照“${record?.name || button.dataset.deleteSnapshot}”？已保存的研究记录不会删除，但之后无法用它运行。`)) return;
  await deleteStore('snapshots', button.dataset.deleteSnapshot);
  selectedIds = selectedIds.filter(id => id !== button.dataset.deleteSnapshot);
  await refreshStorage();
});
$('#missing-snapshots').addEventListener('click', event => {
  const button = event.target.closest?.('[data-remove-missing]');
  if (!button) return;
  selectedIds = selectedIds.filter(id => id !== button.dataset.removeMissing);
  updateSymbols();
});

function selectedRecords() {
  const byId = new Map(packages.map(item => [item.id, item]));
  return selectedIds.map(id => byId.get(id)).filter(Boolean);
}

// Same rules as the local app: choices are kept, only symbols new to the selection are added
// (indexes never), and while a selected snapshot is missing the saved symbols stay and block.
function updateSymbols() {
  const evaluation = evaluateSelection();
  const securities = evaluation.securities;
  const available = Object.keys(securities);
  const incomplete = evaluation.missing.length > 0;
  if (!incomplete) {
    const previous = new Set(selectedSymbols);
    selectedSymbols = available.filter(symbol => previous.has(symbol)
      || (!symbolsPinned && !knownSymbols.has(symbol) && securities[symbol].kind !== 'index'));
    knownSymbols = new Set(available);
    symbolsPinned = false;
  }
  symbolOrder = [...new Set([...symbolOrder, ...selectedSymbols, ...available])]
    .filter(symbol => available.includes(symbol) || selectedSymbols.includes(symbol));
  selectedSymbols = symbolOrder.filter(symbol => selectedSymbols.includes(symbol));
  renderMissing();
  renderSymbols(securities);
  renderBenchmark(securities, incomplete);
  if (!evaluation.dataError && evaluation.range && evaluation.range.first <= evaluation.range.last) {
    const {first, last} = evaluation.range;
    $('#start').min = first; $('#start').max = last;
    $('#end').min = first; $('#end').max = last;
    if (!$('#start').value || $('#start').value < first || $('#start').value > last) $('#start').value = first;
    if (!$('#end').value || $('#end').value > last || $('#end').value < first) $('#end').value = last;
    const sessions = [...new Set(evaluation.selected.flatMap(item => item.sessions || []).filter(day => day >= first && day <= last))].sort();
    if (!$('#test-start').value || !sessions.includes($('#test-start').value)) $('#test-start').value = sessions[Math.floor(sessions.length * .7)] || last;
  }
  renderStrategyParameters(false);
  refreshRunButton();
  saveDraft();
}

// Index series are signal inputs, never benchmarks to buy. A restored benchmark whose snapshot is
// missing stays selected, marked unavailable, and blocks the run; a manual choice whose snapshot
// was deselected resets to none, as in the local app.
function renderBenchmark(securities, incomplete) {
  const select = $('#benchmark');
  const restoring = pendingBenchmark !== null;
  const wanted = restoring ? pendingBenchmark : select.value;
  const marked = !!select.selectedOptions?.[0]?.dataset?.unavailable;
  const usable = Object.entries(securities).filter(([, security]) => security.kind !== 'index');
  const found = usable.some(([symbol]) => symbol === wanted);
  const keep = !!wanted && !found && (restoring || marked || incomplete);
  select.innerHTML = '<option value="">不设置</option>'
    + usable.map(([symbol, security]) => `<option value="${esc(symbol)}">${esc(security.name || symbol)}（${esc(symbol)}）</option>`).join('')
    + (keep ? `<option value="${esc(wanted)}" data-unavailable="true">当前不可用 · ${esc(wanted)}</option>` : '');
  select.value = found || keep ? wanted : '';
  pendingBenchmark = null;
}

function renderSymbols(securities) {
  const host = $('#symbol-list');
  if (!symbolOrder.length) {
    host.innerHTML = '<span class="help">先勾选数据快照。</span>';
    $('#symbol-count').textContent = '';
    return;
  }
  host.innerHTML = symbolOrder.map(symbol => {
    const security = securities[symbol], on = selectedSymbols.includes(symbol), first = selectedSymbols[0] === symbol;
    const label = security ? `${security.name || symbol}（${symbol}）` : `当前不可用 · ${symbol}`;
    const classes = ['chip', 'symbol-chip', on ? 'on' : '', security ? '' : 'missing', security?.kind === 'index' ? 'index' : ''].filter(Boolean).join(' ');
    return `<span class="${classes}"><label><input type="checkbox" data-symbol="${esc(symbol)}" ${on ? 'checked' : ''}>${esc(label)}${security?.kind === 'index' ? '，指数' : ''}</label>`
      + (first && selectedSymbols.length > 1 ? '<span class="first-tag">首项</span>' : on && !first ? `<button type="button" data-first-symbol="${esc(symbol)}" title="把它排在第一位">设为首项</button>` : '') + '</span>';
  }).join('');
  $('#symbol-count').textContent = `已选 ${selectedSymbols.length} / ${Object.keys(securities).length}`;
}

function symbolsChanged() {
  renderSymbols(evaluateSelection().securities);
  renderStrategyParameters(false);
  updateStrategyScopeNote();
  refreshRunButton();
  saveDraft();
}
$('#symbol-list').addEventListener('change', event => {
  const symbol = event.target.dataset?.symbol;
  if (!symbol) return;
  const chosen = new Set(selectedSymbols);
  if (event.target.checked) chosen.add(symbol); else chosen.delete(symbol);
  if (!symbolOrder.includes(symbol)) symbolOrder.push(symbol);
  selectedSymbols = symbolOrder.filter(item => chosen.has(item));
  symbolsChanged();
});
$('#symbol-list').addEventListener('click', event => {
  const button = event.target.closest?.('[data-first-symbol]');
  if (!button) return;
  symbolOrder = [button.dataset.firstSymbol, ...symbolOrder.filter(item => item !== button.dataset.firstSymbol)];
  selectedSymbols = symbolOrder.filter(item => selectedSymbols.includes(item));
  symbolsChanged();
});

/* Saved runs */
const SUMMARY_PARSE_LIMIT = 20 * 1024 * 1024;

function strategyLabel(id) { return strategyCatalog.find(item => item.id === id)?.name || id || '—'; }

function resultSummary(result) {
  return {total_return: result.metrics?.total_return ?? null, max_drawdown: result.metrics?.max_drawdown ?? null,
    currency: result.metadata?.currency || ''};
}

// New records store a summary; older ones are read once per page load (very large results are skipped).
function runSummary(run) {
  if (run.summary) return run.summary;
  if (summaryCache.has(run.id)) return summaryCache.get(run.id);
  let summary = null;
  if (typeof run.resultJson === 'string' && run.resultJson.length <= SUMMARY_PARSE_LIMIT) {
    try {
      const result = JSON.parse(run.resultJson);
      if (!result.kind || result.kind === 'single') summary = resultSummary(result);
    } catch (_) { summary = null; }
  }
  summaryCache.set(run.id, summary);
  return summary;
}

// Older records carry no data label; snapshots still kept in this browser can tell.
function runIsSynthetic(run) {
  if (typeof run.synthetic === 'boolean') return run.synthetic;
  const ids = run.snapshotIds || run.request?.snapshotIds || [];
  return packages.some(item => ids.includes(item.id) && item.synthetic);
}
function runIsReference(run) { return run.request?.config?.mode === 'reference_research'; }

function renderRuns() {
  $('#result-count').textContent = runs.length ? `此浏览器保存了 ${runs.length} 次研究` : '尚无本机运行';
  const host = $('#saved-runs');
  for (const id of [...compareIds]) if (!runs.some(run => run.id === id)) compareIds.delete(id);
  if (!runs.length) {
    host.innerHTML = '<p class="help">运行完成的研究会保存在这里，可再次查看、载回配置或叠加比较。</p>';
    updateCompareButton();
    return;
  }
  host.innerHTML = `<div class="table-wrap"><table><thead><tr><th><span class="visually-hidden">选择比较</span></th><th>研究</th><th>区间</th><th class="num">收益</th><th class="num">最大回撤</th><th>操作</th></tr></thead><tbody>${runs.slice(0, 50).map(run => {
    const request = run.request || {}, config = request.config || {}, kind = request.kind || 'single';
    const summary = kind === 'single' ? runSummary(run) : null;
    const synthetic = runIsSynthetic(run), reference = runIsReference(run);
    return `<tr><td>${kind === 'single' ? `<input type="checkbox" data-compare-run="${esc(run.id)}" ${compareIds.has(run.id) ? 'checked' : ''} aria-label="选择比较">` : ''}</td>`
      + `<td><div class="run-cell">${UI.seal(run.id, 'small')}<div><button type="button" class="link" data-open-run="${esc(run.id)}">${esc(strategyLabel(request.strategy))}</button>`
      + `<small>${esc(Results.KIND_NAMES[kind] || kind)}，${esc(UI.formatDateTime(run.created))}${synthetic || reference ? ' ' + Results.dataBadge({synthetic, reference}) : ''}</small></div></div></td>`
      + `<td class="num">${esc(config.start || '')}<small>${esc(config.end || '')}</small></td>`
      + `<td class="num ${UI.tone(summary?.total_return)}">${UI.formatPercent(summary?.total_return)}</td><td class="num">${UI.formatPercent(summary?.max_drawdown)}</td>`
      + `<td><div class="actions"><button type="button" class="small quiet" data-reload-run="${esc(run.id)}">载入配置</button><button type="button" class="small quiet danger" data-delete-run="${esc(run.id)}">删除</button></div></td></tr>`;
  }).join('')}</tbody></table></div>${runs.length > 50 ? '<p class="help">只列出最近 50 次；更早的记录仍保存在此浏览器，也会包含在本机备份中。</p>' : ''}`;
  updateCompareButton();
}

function updateCompareButton() {
  $('#compare-runs').disabled = compareIds.size < 2 || compareIds.size > 6;
  $('#compare-runs').textContent = compareIds.size ? `比较所选（${compareIds.size}）` : '比较所选';
}

function openSavedRun(id) {
  const run = runs.find(item => item.id === id);
  if (!run) return;
  try { renderResult(JSON.parse(run.resultJson), run); }
  catch (error) { notice(`无法读取这次研究的结果：${error.message}`, true); }
}

$('#saved-runs').addEventListener('change', event => {
  const id = event.target.dataset?.compareRun;
  if (!id) return;
  if (event.target.checked) {
    if (compareIds.size >= 6) { event.target.checked = false; notice('一次最多比较 6 次研究。', true); return; }
    compareIds.add(id);
  } else compareIds.delete(id);
  updateCompareButton();
});
$('#saved-runs').addEventListener('click', async event => {
  const open = event.target.closest?.('[data-open-run]');
  if (open) return openSavedRun(open.dataset.openRun);
  const reload = event.target.closest?.('[data-reload-run]');
  if (reload) {
    const run = runs.find(item => item.id === reload.dataset.reloadRun);
    if (run) loadRunIntoForm(run);
    return;
  }
  const remove = event.target.closest?.('[data-delete-run]');
  if (!remove) return;
  const run = runs.find(item => item.id === remove.dataset.deleteRun);
  if (!window.confirm(`从此浏览器删除研究“${strategyLabel(run?.request?.strategy)}”（${UI.formatDateTime(run?.created)}）？没有本机备份时无法恢复。`)) return;
  await deleteStore('runs', remove.dataset.deleteRun);
  compareIds.delete(remove.dataset.deleteRun);
  if (resultView?.run?.id === remove.dataset.deleteRun) showEmptyResult('这次研究已删除。');
  await refreshStorage();
});
$('#compare-runs').addEventListener('click', () => renderComparison());

/* Results */
const chartHosts = new Map();
function drawChart(host, options) {
  if (!host || !options) return;
  chartHosts.set(host, Charts.render(host, options));
}
function clearCharts() {
  for (const chart of chartHosts.values()) chart?.destroy();
  chartHosts.clear();
}

function showEmptyResult(text) {
  clearCharts();
  resultView = null;
  $('#result-detail').innerHTML = `<div class="empty"><h3>还没有选择结果</h3><p>${esc(text)}</p></div>`;
}

// The single result for the selected window or section. Experiment benchmarks sit beside each part.
function partResult() {
  const result = resultView.result;
  if (result.kind === 'rolling') {
    const window = result.samples?.[resultView.sample];
    if (!window?.result?.metrics) return null;
    // A window keeps only benchmark metrics, so there is no benchmark curve to overlay.
    return {...window.result, benchmark: window.result.benchmark || (window.benchmark ? {metrics: window.benchmark, curve: []} : null)};
  }
  if (result.kind === 'holdout') {
    const part = result[resultView.section];
    if (!part?.result?.metrics) return null;
    return {...part.result, benchmark: part.result.benchmark || part.benchmark || null};
  }
  return result;
}

const TABLE_NAMES = {trades: '成交', orders: '信号与订单', ledger: '账户账本'};
const CSV_NAMES = {...TABLE_NAMES, curve: '每日账户曲线'};

function singleDetail(single, run) {
  const metadata = single.metadata || {}, currency = metadata.currency || '', request = run?.request || {};
  const seriesButtons = Results.CURVE_SERIES.map(item => `<button type="button" data-series="${item.key}" aria-pressed="${item.key === resultView.series}">${esc(item.label)}</button>`).join('');
  return (metadata.research_warning ? `<p class="callout demo">${esc(metadata.research_warning)}</p>` : '')
    + (run && runIsSynthetic(run) ? '<p class="callout demo">合成演示数据，不是真实行情，不构成投资结论。</p>' : '')
    + Results.diagnostics(metadata)
    + Results.metrics(single.metrics || {}, {currency})
    + `<div class="chart-toolbar"><div class="segmented" role="group" aria-label="选择曲线">${seriesButtons}</div>`
    + (single.benchmark?.curve?.length ? `<label><input type="checkbox" data-toggle-benchmark ${resultView.benchmark ? 'checked' : ''}>叠加基准</label>` : '') + '</div>'
    + '<div class="chart" id="result-chart"></div><div class="chart drawdown-chart" id="drawdown-chart"></div>'
    + `<p class="help">时间加权净值剔除了入金和提款的影响；入金不计为投资利润。${single.benchmark?.curve?.length ? '基准使用相同期间与资金流。' : ''}</p>`
    + (metadata.assumptions ? `<p class="help">${esc(metadata.assumptions)}${metadata.survivorship_bias ? '。' + esc(metadata.survivorship_bias) : ''}</p>` : '')
    + `<div class="section-head"><h4>逐笔核对</h4><div class="segmented" role="group" aria-label="明细表">`
    + Object.entries(TABLE_NAMES).map(([name, label]) => `<button type="button" data-table="${name}" aria-pressed="${resultView.table === name}">${label}</button>`).join('')
    + `</div></div><div id="result-table"></div>`
    + `<div class="row csv-row"><span class="muted">导出 CSV：</span>${Object.entries(CSV_NAMES).map(([name, label]) => `<button type="button" class="small" data-csv="${name}">${label}</button>`).join('')}</div>`
    + (single.benchmark?.metrics ? `<details><summary>基准结果（${esc(request.config?.benchmark || '')}）</summary>${Results.metrics(single.benchmark.metrics, {currency})}</details>` : '')
    + Results.cashFlowPlan(metadata)
    + `<details data-json><summary>完整结果 JSON</summary><pre></pre></details>`;
}

function drawCharts() {
  const single = resultView?.single;
  if (!single?.metrics) return;
  drawChart($('#result-chart'), Results.curveChart(single, resultView.series, {benchmark: resultView.benchmark, height: 280}));
  const drawdown = $('#drawdown-chart');
  if (!drawdown) return;
  drawdown.hidden = resultView.series === 'drawdown';
  if (!drawdown.hidden) drawChart(drawdown, {...Results.curveChart(single, 'drawdown', {height: 120}), legend: false, ariaLabel: '回撤'});
}

function drawTable() {
  const single = resultView?.single, host = $('#result-table');
  if (!host || !single) return;
  const rows = single[resultView.table] || [], start = resultView.page * 100;
  host.innerHTML = Results.table(rows.slice(start, start + 100), Results.TABLE_COLUMNS[resultView.table])
    + (rows.length > 100 ? `<div class="table-foot"><button type="button" class="small" data-page="-1" ${start === 0 ? 'disabled' : ''}>上一页</button><span>${start + 1}–${Math.min(start + 100, rows.length)} / ${rows.length}</span><button type="button" class="small" data-page="1" ${start + 100 >= rows.length ? 'disabled' : ''}>下一页</button></div>` : '');
}

function resultHeader(run, result) {
  const request = run?.request || {}, config = request.config || {};
  const kind = result.kind || 'single';
  const badge = run ? Results.dataBadge({synthetic: runIsSynthetic(run), reference: runIsReference(run)}) : '';
  return `<div class="result-head"><div class="result-title">${run ? UI.seal(run.id) : ''}<div><h3>${esc(run ? strategyLabel(request.strategy) : '研究结果')}</h3>`
    + `<p>${esc(Results.KIND_NAMES[kind] || kind)}${config.start ? `，${esc(config.start)} 至 ${esc(config.end)}` : ''}${result.result_hash ? `，结果摘要 ${esc(result.result_hash.slice(0, 12))}` : ''}</p></div></div>`
    + `<div class="row">${badge}${run ? '<button type="button" class="small" data-reload-current>载入到配置</button>' : ''}</div></div>`;
}

function renderResult(result, run = null) {
  resultView = {result, run, sample: 0, section: 'holdout', series: 'twr_index', benchmark: true, table: 'trades', page: 0, single: null};
  drawResult();
  $('#result-detail').scrollIntoView?.({behavior: 'smooth', block: 'start'});
}

function drawResult() {
  const {result, run} = resultView, detail = $('#result-detail');
  const kind = result.kind || 'single';
  const single = resultView.single = partResult();
  let body;
  if (kind === 'rolling') {
    const summary = result.summary || {}, window = result.samples?.[resultView.sample];
    body = `<div class="metric-grid">${[['已完成窗口', UI.formatNumber(summary.count, 0)], ['跳过窗口', UI.formatNumber(summary.skipped, 0)],
      ['亏损比例', UI.formatPercent(summary.loss_ratio)], ['收益中位数', UI.formatPercent(summary.median), summary.median],
      ['10% 分位', UI.formatPercent(summary.q10), summary.q10], ['90% 分位', UI.formatPercent(summary.q90), summary.q90],
      ['最好窗口', UI.formatPercent(summary.best), summary.best], ['最差窗口', UI.formatPercent(summary.worst), summary.worst]]
      .map(([label, value, raw]) => Results.metric(label, value, {tone: UI.tone(raw)})).join('')}</div>`
      + Results.rollingHeatmap({summary, metadata: result.metadata, samples: result.samples}, resultView.sample)
      + `<details><summary>全部窗口（${(result.samples || []).length} 个）</summary><div class="table-wrap"><table><thead><tr><th>起点</th><th>终点</th><th>状态</th><th class="num">收益</th><th>原因</th></tr></thead><tbody>${(result.samples || []).map(item => `<tr><td>${esc(item.start)}</td><td>${esc(item.end || '')}</td><td>${esc(item.status === 'completed' ? '已完成' : '已跳过')}</td><td class="num">${UI.formatPercent(item.metrics?.total_return)}</td><td>${esc(item.reason || '')}</td></tr>`).join('')}</tbody></table></div></details>`
      + (single ? `<h4>第 ${resultView.sample + 1} 个窗口：${esc(window.start)} 至 ${esc(window.end || '')}</h4>${singleDetail(single, run)}`
        : `<p class="callout warn">起点 ${esc(window?.start || '')} 的窗口已跳过：${esc(window?.reason || '没有结果')}</p>`);
  } else if (kind === 'holdout') {
    body = Results.holdoutSummary({metadata: result.metadata, development: result.development?.result?.metrics, holdout: result.holdout?.result?.metrics}, resultView.section)
      + (result.metadata?.parameter_selection ? `<p class="help">${esc(result.metadata.parameter_selection)}。</p>` : '')
      + (single ? `<h4>${resultView.section === 'development' ? '开发区间' : '留出区间'}</h4>${singleDetail(single, run)}` : '<p class="callout warn">这一区间没有可显示的结果。</p>');
  } else {
    body = singleDetail(single, run);
  }
  clearCharts();
  detail.innerHTML = resultHeader(run, result) + body;
  drawCharts();
  drawTable();
}

function currentCsv(name) {
  const single = resultView?.single;
  if (!single) return;
  const rows = single[name] || [];
  const id = resultView.run?.id?.slice(0, 10) || 'result';
  const suffix = resultView.result.kind === 'rolling' ? `-window${resultView.sample + 1}` : resultView.result.kind === 'holdout' ? `-${resultView.section}` : '';
  UI.downloadCsv(UI.toCsv(rows, Results.csvColumns(rows, name)), `investment-lab-${id}${suffix}-${name}.csv`);
}

$('#result-detail').addEventListener('click', event => {
  const target = event.target;
  const open = target.closest?.('[data-open-run]');
  if (open) return openSavedRun(open.dataset.openRun);
  const uncompare = target.closest?.('[data-uncompare]');
  if (uncompare) {
    compareIds.delete(uncompare.dataset.uncompare);
    renderRuns();
    if (compareIds.size >= 2) renderComparison();
    else showEmptyResult('已从比较中移除。勾选至少两次单次回测后再比较。');
    return;
  }
  if (target.closest?.('[data-clear-compare]')) {
    compareIds.clear();
    renderRuns();
    return showEmptyResult('比较已清空。');
  }
  if (!resultView) return;
  const series = target.closest?.('[data-series]');
  if (series) {
    resultView.series = series.dataset.series;
    document.querySelectorAll('#result-detail [data-series]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.series === resultView.series)));
    return drawCharts();
  }
  const table = target.closest?.('[data-table]');
  if (table) {
    resultView.table = table.dataset.table;
    resultView.page = 0;
    document.querySelectorAll('#result-detail [data-table]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.table === resultView.table)));
    return drawTable();
  }
  const page = target.closest?.('[data-page]');
  if (page) { resultView.page = Math.max(0, resultView.page + Number(page.dataset.page)); return drawTable(); }
  const sample = target.closest?.('[data-sample]');
  if (sample) { resultView.sample = Number(sample.dataset.sample); resultView.page = 0; return drawResult(); }
  const section = target.closest?.('[data-section]');
  if (section) { resultView.section = section.dataset.section; resultView.page = 0; return drawResult(); }
  const csv = target.closest?.('[data-csv]');
  if (csv) return currentCsv(csv.dataset.csv);
  if (target.closest?.('[data-reload-current]') && resultView.run) return loadRunIntoForm(resultView.run);
});
$('#result-detail').addEventListener('change', event => {
  if (!resultView || !event.target.matches?.('[data-toggle-benchmark]')) return;
  resultView.benchmark = event.target.checked;
  drawCharts();
});
// The full JSON is written only when opened; large results would otherwise slow every redraw.
$('#result-detail').addEventListener('toggle', event => {
  const details = event.target;
  if (!details.matches?.('details[data-json]') || !details.open || !resultView) return;
  const pre = details.querySelector('pre');
  if (!pre.textContent) pre.textContent = JSON.stringify(resultView.single || resultView.result, null, 2);
}, true);

function renderComparison() {
  const items = [], skipped = [];
  for (const run of runs.filter(item => compareIds.has(item.id)).slice(0, 6)) {
    let result = null;
    try { result = JSON.parse(run.resultJson); } catch (_) { result = null; }
    if (result?.metrics && (!result.kind || result.kind === 'single')) {
      items.push({id: run.id, title: strategyLabel(run.request?.strategy), config: run.request?.config || {}, metrics: result.metrics,
        currency: result.metadata?.currency || '', synthetic: runIsSynthetic(run), reference: runIsReference(run), curve: result.curve});
    } else skipped.push({id: run.id, problem: '没有可比较的单次回测结果'});
  }
  if (items.length < 2) { notice('请选择 2–6 次已完成的单次回测。', true); return; }
  const view = Results.comparison(items, {skipped, removable: true});
  clearCharts();
  resultView = null;
  $('#result-detail').innerHTML = `<div class="result-head"><div class="result-title"><div><h3>比较 ${items.length} 次单次回测</h3><p>时间加权净值按日期叠加，剔除入金与提款影响。</p></div></div>`
    + '<div class="row"><button type="button" class="small quiet" data-clear-compare>清空比较</button></div></div>'
    + view.warnings + '<div class="chart" id="compare-chart"></div>' + view.table;
  drawChart($('#compare-chart'), view.chart);
  $('#result-detail').scrollIntoView?.({behavior: 'smooth', block: 'start'});
}

/* Load a saved run's settings back into the form */
function loadRunIntoForm(run) {
  const request = run.request || {}, config = request.config || {}, research = request.research || {};
  const text = value => value === null || value === undefined ? '' : String(value);
  const paramsText = JSON.stringify(request.params || {}, null, 2);
  // Keep the other strategies' drafts, captured before the form changes.
  const drafts = strategyEditor.snapshot();
  const values = {strategy: request.strategy, kind: request.kind || 'single', start: config.start, end: config.end, cash: config.initial_cash,
    leverage: config.max_leverage, mode: config.mode, commission: config.commission_bps, 'minimum-commission': config.minimum_commission,
    slippage: config.slippage_bps, interest: config.annual_interest, params: paramsText,
    flows: JSON.stringify(config.cash_flows || {}, null, 2)};
  if (request.kind === 'rolling') Object.assign(values, {interval: research.interval, horizon: research.horizon, 'end-mode': research.end_mode});
  if (request.kind === 'holdout') Object.assign(values, {'test-start': research.test_start, gap: research.gap_sessions});
  for (const [id, value] of Object.entries(values)) if (value !== undefined && $(`#${id}`)) $(`#${id}`).value = text(value);
  selectedIds = [...(request.snapshotIds || run.snapshotIds || [])];
  selectedSymbols = [...(config.symbols || [])];
  symbolOrder = [...selectedSymbols];
  symbolsPinned = true;
  pendingBenchmark = config.benchmark || '';
  strategyEditor.restore({...drafts, [request.strategy]: paramsText});
  renderPackages();
  updateSymbols();
  renderStrategyParameters(false);
  syncMonthlyDeposit();
  updateKind();
  refreshRunButton();
  saveDraft();
  $('#research-form').scrollIntoView?.({behavior: 'smooth', block: 'start'});
  const blocker = runBlocker();
  notice(blocker ? `已载入这次研究的配置。还不能运行：${blocker}` : '已载入这次研究的配置，可以修改后再运行。', !!blocker);
}

/* Import, demo and backups */
async function importPackageJson(packageJson, metadata = {}) {
  const verifiedResponse = await workerRequest('verify', {packageJson});
  const verified = verifiedResponse.value;
  const catalog = {securities:verified.securities, sessions:verified.sessions};
  const record = {id:verified.snapshot_id, name:verified.name, synthetic:verified.synthetic,
    source:verified.source, rows:verified.rows, first:catalog.sessions[0] || '',
    last:catalog.sessions.at(-1) || '', catalog, imported:new Date().toISOString(), packageJson};
  await putStore('snapshots', record);
  // Adding a snapshot keeps the current choices; a restored selection waiting for it is applied exactly.
  selectedIds = [...new Set([...selectedIds, record.id])];
  await refreshStorage();
  $('#package-status').textContent = `${record.synthetic ? '合成演示' : '数据快照'}已校验并保存在此浏览器：${record.name}，${record.rows.toLocaleString()} 行。`;
  if (metadata.pyodide) $('#engine-status').textContent = `固定 Pyodide ${metadata.pyodide} 已就绪；研究数据只在本机浏览器中使用。`;
}

$('#package-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  $('#package-status').textContent = '正在检查 gzip 数据包和快照摘要…';
  try {
    const packageJson = await PortableDataPackage.readGzipText(file);
    await importPackageJson(packageJson);
    notice('快照已导入到此浏览器，不会上传到网站。');
  } catch (error) {
    $('#package-status').textContent = `导入失败：${error.message}`;
    notice(error.message, true);
  } finally { event.target.value = ''; }
});

$('#demo-package').addEventListener('click', async () => {
  const button = $('#demo-package');
  button.disabled = true;
  $('#package-status').textContent = '正在准备五日合成演示（首次需下载计算引擎）…';
  try {
    const response = await workerRequest('demo');
    await importPackageJson(response.packageJson, response);
    notice('已加入明确标记的合成演示数据。');
  } catch (error) {
    $('#package-status').textContent = `无法准备演示：${error.message}`;
    notice(error.message, true);
  } finally { button.disabled = false; }
});

async function gzipText(text) {
  if (typeof CompressionStream !== 'function') throw new Error('当前浏览器不支持 gzip 压缩。');
  return new Response(new Blob([text]).stream().pipeThrough(new CompressionStream('gzip'))).blob();
}

$('#export-backup').addEventListener('click', async () => {
  const button = $('#export-backup'); button.disabled = true;
  try {
    const backup = {format:'investment-lab-browser-backup', format_version:1, created:new Date().toISOString(),
      snapshots:packages.map(({id,name,synthetic,source,rows,first,last,catalog,imported,packageJson})=>({id,name,synthetic,source,rows,first,last,catalog,imported,packageJson})),
      runs};
    if (JSON.stringify(backup).length > 128 * 1024 * 1024) throw new Error('本机备份超过 128 MiB，请先删除不需要的研究记录或分别保存快照。');
    UI.downloadBlob(await gzipText(JSON.stringify(backup)), 'investment-lab-browser-backup.json.gz');
    notice('本机备份已生成到浏览器的下载目录。文件不会自动上传；请自行保存在安全位置。');
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});

$('#backup-file').addEventListener('change', async event => {
  const file = event.target.files?.[0]; if (!file) return;
  $('#package-status').textContent = '正在读取本机备份…';
  try {
    const text = await PortableDataPackage.readGzipText(file);
    const backup = JSON.parse(text);
    if (backup.format !== 'investment-lab-browser-backup' || backup.format_version !== 1 || !Array.isArray(backup.snapshots) || !Array.isArray(backup.runs)) throw new Error('备份格式无效或版本不支持。');
    for (const record of backup.snapshots) {
      const verified = await workerRequest('verify', {packageJson:record.packageJson});
      if (verified.value.snapshot_id !== record.id) throw new Error('备份中的快照编号与数据不匹配。');
      record.catalog = {securities:verified.value.securities, sessions:verified.value.sessions};
      record.first = record.catalog.sessions[0] || '';
      record.last = record.catalog.sessions.at(-1) || '';
      await putStore('snapshots', record);
    }
    for (const run of backup.runs) if (run && typeof run.id === 'string' && typeof run.resultJson === 'string') await putStore('runs', run);
    // A restored backup starts a fresh selection: every snapshot, with all tradable symbols.
    selectedIds = backup.snapshots.map(item => item.id);
    selectedSymbols = [];
    symbolOrder = [];
    knownSymbols = new Set();
    symbolsPinned = false;
    await refreshStorage();
    $('#package-status').textContent = `已恢复 ${backup.snapshots.length} 个快照与 ${backup.runs.length} 条本机研究记录。`;
    notice('备份已恢复到此浏览器。');
  } catch (error) { $('#package-status').textContent = `恢复失败：${error.message}`; notice(error.message, true); }
  finally { event.target.value = ''; }
});

/* Run */
function parseJsonField(id, label) {
  try { return JSON.parse($(`#${id}`).value); }
  catch (error) { throw new Error(`${label}不是有效 JSON：${error.message}`); }
}

function buildRunRequest() {
  const evaluation = evaluateSelection();
  if (evaluation.error) throw new Error(evaluation.error);
  const records = selectedRecords();
  if (!records.length) throw new Error('请先选择一个数据快照。');
  if (!selectedSymbols.length) throw new Error('请至少勾选一个交易标的。');
  const params = strategyEditor.value();
  const packagesJson = records.map(record => record.packageJson);
  const totalBytes = packagesJson.reduce((sum, value) => sum + new Blob([value]).size, 0);
  if (totalBytes > 128 * 1024 * 1024) throw new Error('本次选择的快照总量超过 128 MiB，请减少标的或拆分研究。');
  const config = {
    start:$('#start').value, end:$('#end').value, symbols:[...selectedSymbols],
    initial_cash:$('#cash').value, max_leverage:$('#leverage').value, mode:$('#mode').value,
    commission_bps:$('#commission').value, minimum_commission:$('#minimum-commission').value,
    slippage_bps:$('#slippage').value, annual_interest:$('#interest').value,
    benchmark:$('#benchmark').value || null, cash_flows:parseJsonField('flows', '资金流'),
    allow_unverified_actions:false,
  };
  const request = {run_id:crypto.randomUUID ? crypto.randomUUID() : String(Date.now()), packages:packagesJson,
    config, strategy:$('#strategy').value, params, kind:$('#kind').value};
  request.engine_manifest_hash = runtimeManifestHash;
  request.strategy_source_hash = runtimeManifest.files['runtime/investment_lab/strategies/examples.py'];
  request.strategy_catalog_hash = runtimeManifest.files['runtime/investment_lab/strategies/catalog.json'];
  if (request.kind === 'rolling') request.research = {interval:$('#interval').value, horizon:Number($('#horizon').value), end_mode:$('#end-mode').value};
  if (request.kind === 'holdout') request.research = {test_start:$('#test-start').value, gap_sessions:Number($('#gap').value)};
  return request;
}

function setProgress(percent, text, eta, indeterminate = false) {
  $('#progress-area').hidden = false;
  $('#progress-fill').style.width = `${percent}%`;
  const track = $('#progress-area').querySelector('[role="progressbar"]');
  track.classList.toggle('indeterminate', indeterminate);
  if (indeterminate) track.removeAttribute('aria-valuenow'); else track.setAttribute('aria-valuenow', String(Math.round(percent)));
  $('#progress-text').textContent = text;
  $('#progress-eta').textContent = eta;
}

$('#research-form').addEventListener('submit', async event => {
  event.preventDefault();
  let request;
  try { request = buildRunRequest(); }
  catch (error) { notice(error.message, true); return; }
  running = true;
  refreshRunButton();
  setProgress(0, '正在准备本机计算…', '首次启动需下载引擎', true);
  $('#engine-status').textContent = '正在启动隔离的浏览器 Worker；首次加载会下载固定版本运行环境。';
  const progressStarted = Date.now();
  let progressRate = null;
  try {
    const response = await workerRequest('run', {request}, message => {
      const percent = message.total ? Math.min(100, message.done / message.total * 100) : 0;
      const elapsed = Math.max(.001, (Date.now() - progressStarted) / 1000);
      const rate = message.done > 0 ? message.done / elapsed : null;
      if (rate) progressRate = progressRate ? progressRate * .65 + rate * .35 : rate;
      setProgress(percent, `计算进度 ${message.done} / ${message.total}（${percent.toFixed(1)}%）`,
        progressRate ? `预计剩余 ${UI.formatDuration((message.total - message.done) / progressRate)}` : '预计剩余：计算中');
    });
    const result = response.value;
    const resultJson = JSON.stringify(result);
    const title = `${request.strategy} · ${request.kind} · ${request.config.start}—${request.config.end}`;
    const {packages:ignoredPackages, ...frozenRequest} = request;
    frozenRequest.snapshotIds = selectedIds.slice();
    frozenRequest.strategyDefinition = strategyEditor.entry();
    frozenRequest.strategySource = strategySource;
    // The summary and data label let the saved list render without parsing every stored result.
    const summary = request.kind === 'single' ? resultSummary(result) : null;
    const synthetic = selectedRecords().some(item => item.synthetic);
    const record = {id:request.run_id, created:new Date().toISOString(), title, snapshotIds:selectedIds.slice(), request:frozenRequest, resultJson, summary, synthetic};
    await putStore('runs', record);
    runs = (await listStore('runs')).sort((a, b) => b.created.localeCompare(a.created));
    renderRuns();
    renderResult(result, record);
    setProgress(100, '计算完成', '预计剩余：0 秒');
    $('#engine-status').textContent = `计算由 Pyodide ${response.pyodide} Worker 完成；结果只保存在此浏览器。`;
    notice(`回测已完成并保存在此浏览器。结果摘要：${result.result_hash.slice(0, 16)}`);
  } catch (error) {
    setProgress(0, '计算失败', error.message);
    $('#engine-status').textContent = '请检查数据覆盖、价格模式和研究参数后重试。';
    notice(error.message, true);
  } finally {
    running = false;
    refreshRunButton();
  }
});

function updateKind() {
  $('#rolling-fields').hidden = $('#kind').value !== 'rolling';
  $('#holdout-fields').hidden = $('#kind').value !== 'holdout';
}
$('#kind').addEventListener('change', updateKind);
$('#mode').addEventListener('change', updateSelectionNote);
for (const id of FIELD_IDS) $(`#${id}`).addEventListener('input', saveDraft);
for (const id of FIELD_IDS) $(`#${id}`).addEventListener('change', saveDraft);
for (const id of ['start', 'end', 'benchmark', 'kind']) $(`#${id}`).addEventListener('change', refreshRunButton);
$('#research-form').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    if (!$('#run').disabled) $('#research-form').requestSubmit();
  }
});

/* Appearance */
function initPreferences() {
  const storage = UI.safeStorage(window);
  let prefs = UI.readPreferences(storage);
  const render = () => {
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === prefs.theme)));
    document.querySelectorAll('[data-gain-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.gainChoice === prefs.gain)));
  };
  render();
  const panel = $('#prefs-panel'), button = $('#prefs-button');
  const toggle = open => { panel.hidden = !open; button.setAttribute('aria-expanded', String(open)); };
  button.addEventListener('click', () => toggle(panel.hidden));
  panel.addEventListener('click', event => {
    const choice = event.target.closest?.('[data-theme-choice],[data-gain-choice]');
    if (!choice) return;
    prefs = {...prefs, ...(choice.dataset.themeChoice ? {theme: choice.dataset.themeChoice} : {gain: choice.dataset.gainChoice})};
    UI.applyPreferences(prefs);
    UI.savePreferences(storage, prefs);
    render();
  });
  document.addEventListener('click', event => {
    if (!panel.hidden && !panel.contains(event.target) && !button.contains(event.target)) toggle(false);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { toggle(false); button.focus(); } });
}

async function initialize() {
  initPreferences();
  try {
    const [opened] = await Promise.all([openDatabase(), loadStrategyCatalog()]);
    database = opened;
    restoreDraft();
    await refreshStorage();
  } catch (error) {
    $('#storage-label').textContent = '本机存储不可用';
    notice(`无法启动浏览器版：${error.message}`, true);
    $('#run').disabled = true;
  }
}

async function loadStrategyCatalog() {
  const [catalogResponse, manifestResponse, sourceResponse] = await Promise.all([
    fetch('./strategy-catalog.json', {cache:'no-store'}),
    fetch('./runtime-manifest.json', {cache:'no-store'}),
    fetch('./runtime/investment_lab/strategies/examples.py', {cache:'no-store'}),
  ]);
  if (!catalogResponse.ok) throw new Error(`无法读取内置策略目录（${catalogResponse.status}）。`);
  if (!manifestResponse.ok) throw new Error(`无法读取浏览器计算版本清单（${manifestResponse.status}）。`);
  if (!sourceResponse.ok) throw new Error(`无法读取内置策略程序（${sourceResponse.status}）。`);
  const catalogText = await catalogResponse.text();
  const manifestText = await manifestResponse.text();
  strategySource = await sourceResponse.text();
  const catalog = JSON.parse(catalogText);
  runtimeManifest = JSON.parse(manifestText);
  runtimeManifestHash = await sha256Hex(manifestText);
  const catalogHash = await sha256Hex(catalogText);
  const sourceHash = await sha256Hex(strategySource);
  if (catalogHash !== runtimeManifest.files?.['strategy-catalog.json'] ||
      catalogHash !== runtimeManifest.files?.['runtime/investment_lab/strategies/catalog.json'] ||
      sourceHash !== runtimeManifest.files?.['runtime/investment_lab/strategies/examples.py']) {
    throw new Error('策略目录与计算程序版本不一致；请刷新页面或稍后重试。');
  }
  if (!Array.isArray(catalog.strategies) || catalog.strategies.length !== 22) throw new Error('内置策略目录不完整；请刷新页面或稍后重试。');
  strategyCatalog = catalog.strategies;
  const groups = new Map();
  for (const item of strategyCatalog) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  const select = $('#strategy'), selected = select.value;
  select.innerHTML = [...groups].map(([category, items]) => `<optgroup label="${esc(category)}">${items.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</optgroup>`).join('');
  if (strategyCatalog.some(item => item.id === selected)) select.value = selected;
  else if (strategyCatalog.length) select.value = strategyCatalog[0].id;
}

initialize();
