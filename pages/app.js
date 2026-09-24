const $ = selector => document.querySelector(selector);
const DB_NAME = 'investment-lab-pages';
const DB_VERSION = 1;
const DRAFT_KEY = 'investment-lab-pages-draft-v1';
const FIELD_IDS = ['strategy','kind','start','end','cash','leverage','mode','benchmark','commission','minimum-commission','slippage','interest','params','flows','interval','horizon','end-mode','test-start','gap'];
const pending = new Map();
let database;
let worker;
let packages = [];
let runs = [];
let selectedIds = [];
let selectedSymbols = [];

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function notice(message, error = false) {
  const element = $('#notice');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = false;
  if (!error) setTimeout(() => { element.hidden = true; }, 7000);
}

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

async function listStore(name) {
  return requestResult(database.transaction(name, 'readonly').objectStore(name).getAll());
}

async function putStore(name, value) {
  return requestResult(database.transaction(name, 'readwrite').objectStore(name).put(value));
}

async function deleteStore(name, key) {
  return requestResult(database.transaction(name, 'readwrite').objectStore(name).delete(key));
}

function getWorker() {
  if (worker) return worker;
  worker = new Worker(new URL('./worker.js', import.meta.url), {type:'module'});
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

function draftValues() {
  return Object.fromEntries(FIELD_IDS.map(id => [id, $(`#${id}`).value]));
}

function saveDraft() {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify({fields:draftValues(), selectedIds, selectedSymbols})); }
  catch (_) { notice('浏览器无法保存自动草稿；请导出本机数据备份。', true); }
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    for (const [id, value] of Object.entries(draft.fields || {})) {
      if (FIELD_IDS.includes(id) && $(`#${id}`)) $(`#${id}`).value = value;
    }
    selectedIds = Array.isArray(draft.selectedIds) ? draft.selectedIds : [];
    selectedSymbols = Array.isArray(draft.selectedSymbols) ? draft.selectedSymbols : [];
  } catch (_) { /* A damaged draft falls back to safe form defaults. */ }
  refreshGuidedInputs();
}

const strategyHelp = {
  buy_hold: '买入持有：首次满足数据与成交条件时建立目标仓位，之后持有。目标仓位等高级参数可在下方 JSON 中调整。',
  dca: '定期投入：每月首个交易日尝试按“每次买入金额”下单；每月自动入金是另一回事，只给账户补充现金。',
  monthly_equal_weight: '月度定投与动态等权：首次配置及月度检查时用可用现金优先补低配标的；可选择是否在权重偏离时卖出再平衡。',
  moving_average: '均线：比较已知收盘价与历史均线，达到条件后建立或退出目标仓位；窗口长度可在高级策略参数中调整。',
  drawdown_buy: '回撤买入：价格相对历史窗口高点跌到设定阈值时建立目标仓位；窗口和回撤阈值在高级策略参数中调整。',
  rotation: '同市场轮动：按历史动量在所选标的中选择领先者，每月检查；多标的会共用同一账户。',
  leverage_rebalance: '杠杆再平衡：每日尝试恢复目标杠杆，融资、费用及成交限制会影响结果；请谨慎检查假设。',
  futures_roll: '月份合约换月：按你预先提供的固定日期安排换月，不会使用未来成交量挑选合约。',
  cash: '持有现金：不下买单，可用来核对资金流、区间和结果展示。',
};

function refreshGuidedInputs() {
  const strategy = $('#strategy').value;
  $('#strategy-help').textContent = strategyHelp[strategy] || '当前策略可在高级设置的 JSON 参数中配置。';
  $('#dca-amount-row').hidden = strategy !== 'dca';
  $('#equal-weight-mode-row').hidden = strategy !== 'monthly_equal_weight';
  const errors = [];
  try {
    if (document.activeElement !== $('#monthly-deposit-guide')) $('#monthly-deposit-guide').value = InvestmentLabUI.readAmount($('#flows').value, 'monthly', '资金流');
    $('#monthly-deposit-guide').disabled = false;
  } catch (error) { $('#monthly-deposit-guide').disabled = true; errors.push(error.message); }
  try {
    if (document.activeElement !== $('#dca-amount-guide')) $('#dca-amount-guide').value = InvestmentLabUI.readAmount($('#params').value, 'amount', '策略参数') || '1000';
    if (document.activeElement !== $('#equal-weight-mode-guide')) $('#equal-weight-mode-guide').value = InvestmentLabUI.readChoice($('#params').value, 'portfolio_mode', 'rebalance', ['rebalance', 'no_rebalance'], '策略参数');
    $('#dca-amount-guide').disabled = false;
    $('#equal-weight-mode-guide').disabled = false;
  } catch (error) {
    $('#dca-amount-guide').disabled = true;
    $('#equal-weight-mode-guide').disabled = true;
    errors.push(error.message);
  }
  $('#guide-status').textContent = errors.length ? `${errors.join(' ')} 请展开高级设置修正 JSON。` : '上方常用输入会写入下方参数并保留其他内容；特殊日期仍可在高级 JSON 中填写。';
  $('#guide-status').classList.toggle('guide-error', errors.length > 0);
}

function writeGuidedAmount(inputId, textareaId, key, label) {
  try {
    const input = $(`#${inputId}`);
    $(`#${textareaId}`).value = InvestmentLabUI.writeAmount($(`#${textareaId}`).value, key, input.value, label);
    $(`#${textareaId}`).dispatchEvent(new Event('input', {bubbles:true}));
  } catch (error) {
    $('#guide-status').textContent = error.message;
    $('#guide-status').classList.add('guide-error');
  }
}

$('#monthly-deposit-guide').addEventListener('input', () => writeGuidedAmount('monthly-deposit-guide', 'flows', 'monthly', '资金流'));
$('#dca-amount-guide').addEventListener('input', () => writeGuidedAmount('dca-amount-guide', 'params', 'amount', '策略参数'));
$('#monthly-deposit-guide').addEventListener('blur', refreshGuidedInputs);
$('#dca-amount-guide').addEventListener('blur', refreshGuidedInputs);
$('#equal-weight-mode-guide').addEventListener('change', () => {
  try {
    $('#params').value = InvestmentLabUI.writeChoice($('#params').value, 'portfolio_mode', $('#equal-weight-mode-guide').value,
      ['rebalance', 'no_rebalance'], '策略参数');
    $('#params').dispatchEvent(new Event('input', {bubbles:true}));
  } catch (error) { $('#guide-status').textContent = error.message; $('#guide-status').classList.add('guide-error'); }
});
$('#strategy').addEventListener('change', refreshGuidedInputs);
$('#flows').addEventListener('input', refreshGuidedInputs);
$('#params').addEventListener('input', refreshGuidedInputs);

async function refreshStorage() {
  packages = await listStore('snapshots');
  runs = (await listStore('runs')).sort((a,b) => b.created.localeCompare(a.created));
  $('#storage-label').textContent = `本机保存 ${packages.length} 个快照 · ${runs.length} 次研究`;
  $('#export-backup').disabled = packages.length === 0 && runs.length === 0;
  renderPackages();
  renderRuns();
  updateSymbols();
}

function renderPackages() {
  const host = $('#snapshot-list');
  if (!packages.length) {
    host.innerHTML = '<div class="empty"><span>＋</span><p>导入快照数据包，或先添加五日合成样本试用流程。</p></div>';
    return;
  }
  host.innerHTML = packages.map(item => `<div class="snapshot-card"><input type="checkbox" data-select-snapshot="${escapeHtml(item.id)}" ${selectedIds.includes(item.id)?'checked':''}><span><span class="snapshot-name">${escapeHtml(item.name)}</span><span class="snapshot-meta">${item.synthetic?'合成演示':'真实来源'} · ${escapeHtml(item.first)} → ${escapeHtml(item.last)} · ${Number(item.rows).toLocaleString()} 行 · ${escapeHtml(item.id.slice(0,12))}</span></span><button type="button" class="snapshot-delete" data-delete-snapshot="${escapeHtml(item.id)}">移除本机副本</button></div>`).join('');
  host.querySelectorAll('[data-select-snapshot]').forEach(input => input.addEventListener('change', () => {
    selectedIds = [...host.querySelectorAll('[data-select-snapshot]:checked')].map(node => node.dataset.selectSnapshot);
    selectedSymbols = [];
    saveDraft();
    updateSymbols();
  }));
  host.querySelectorAll('[data-delete-snapshot]').forEach(button => button.addEventListener('click', async () => {
    await deleteStore('snapshots', button.dataset.deleteSnapshot);
    selectedIds = selectedIds.filter(id => id !== button.dataset.deleteSnapshot);
    selectedSymbols = [];
    saveDraft();
    await refreshStorage();
  }));
}

function selectedRecords() {
  const byId = new Map(packages.map(item => [item.id, item]));
  return selectedIds.map(id => byId.get(id)).filter(Boolean);
}

function updateSymbols() {
  const records = selectedRecords();
  const securities = new Map();
  const calendars = [];
  for (const record of records) {
    const {catalog, migrated} = InvestmentLabUI.snapshotCatalog(record);
    if (migrated) putStore('snapshots', record).catch(() => {});
    for (const [symbol, security] of Object.entries(catalog.securities || {})) {
      if (!securities.has(symbol)) securities.set(symbol, security);
    }
    const sessions = catalog.sessions || [];
    if (sessions.length) calendars.push({first:sessions[0], last:sessions[sessions.length-1], sessions});
  }
  const intersectionStart = calendars.length ? calendars.map(item => item.first).sort().at(-1) : '';
  const intersectionEnd = calendars.length ? calendars.map(item => item.last).sort()[0] : '';
  if (!selectedSymbols.length) selectedSymbols = [...securities.keys()];
  selectedSymbols = selectedSymbols.filter(symbol => securities.has(symbol));
  const symbolsHost = $('#symbol-list');
  if (!securities.size) {
    symbolsHost.innerHTML = '';
    $('#benchmark').innerHTML = '<option value="">不设置</option>';
    $('#run').disabled = true;
    return;
  }
  symbolsHost.innerHTML = `<strong class="help">交易标的：</strong>` + [...securities.entries()].map(([symbol, security]) => `<label class="symbol-choice"><input type="checkbox" data-symbol="${escapeHtml(symbol)}" ${selectedSymbols.includes(symbol)?'checked':''}>${escapeHtml(security.name || symbol)} · ${escapeHtml(symbol)}</label>`).join('');
  symbolsHost.querySelectorAll('[data-symbol]').forEach(input => input.addEventListener('change', () => {
    selectedSymbols = [...symbolsHost.querySelectorAll('[data-symbol]:checked')].map(node => node.dataset.symbol);
    saveDraft();
  }));
  const previousBenchmark = $('#benchmark').value;
  $('#benchmark').innerHTML = '<option value="">不设置</option>' + [...securities.keys()].map(symbol => `<option value="${escapeHtml(symbol)}">${escapeHtml(symbol)}</option>`).join('');
  if (securities.has(previousBenchmark)) $('#benchmark').value = previousBenchmark;
  if (intersectionStart && intersectionEnd && intersectionStart <= intersectionEnd) {
    $('#start').min = intersectionStart; $('#start').max = intersectionEnd;
    $('#end').min = intersectionStart; $('#end').max = intersectionEnd;
    if (!$('#start').value || $('#start').value < intersectionStart || $('#start').value > intersectionEnd) $('#start').value = intersectionStart;
    if (!$('#end').value || $('#end').value > intersectionEnd || $('#end').value < intersectionStart) $('#end').value = intersectionEnd;
    const combinedSessions = [...new Set(calendars.flatMap(item => item.sessions).filter(day => day >= intersectionStart && day <= intersectionEnd))].sort();
    if (!$('#test-start').value || !combinedSessions.includes($('#test-start').value)) $('#test-start').value = combinedSessions[Math.floor(combinedSessions.length * .7)] || intersectionEnd;
  }
  $('#run').disabled = selectedIds.length === 0 || selectedSymbols.length === 0;
  saveDraft();
}

function renderRuns() {
  $('#result-count').textContent = runs.length ? `${runs.length} 次本机研究` : '尚无本机运行';
  const host = $('#saved-runs');
  host.innerHTML = runs.slice(0, 20).map(run => `<div class="saved-run"><button type="button" class="text-button" data-open-run="${escapeHtml(run.id)}">${escapeHtml(run.title)} · ${escapeHtml(new Date(run.created).toLocaleString())}</button><button type="button" class="snapshot-delete" data-delete-run="${escapeHtml(run.id)}">删除</button></div>`).join('');
  host.querySelectorAll('[data-open-run]').forEach(button => button.addEventListener('click', () => {
    const run = runs.find(item => item.id === button.dataset.openRun);
    if (run) renderResult(JSON.parse(run.resultJson));
  }));
  host.querySelectorAll('[data-delete-run]').forEach(button => button.addEventListener('click', async () => {
    await deleteStore('runs', button.dataset.deleteRun);
    await refreshStorage();
  }));
}

function formatPercent(value) { return value == null ? '—' : `${(Number(value)*100).toFixed(2)}%`; }
function formatNumber(value) { return value == null ? '—' : Number(value).toLocaleString(undefined,{maximumFractionDigits:2}); }

function metricCards(metrics, currency) {
  const values = [
    ['总收益', formatPercent(metrics.total_return)], ['年化收益', formatPercent(metrics.annualized_return)],
    ['最大回撤', formatPercent(metrics.max_drawdown)], ['期末权益', `${escapeHtml(currency)} ${formatNumber(metrics.final_equity)}`],
    ['成交笔数', formatNumber(metrics.trades)], ['胜率', formatPercent(metrics.win_rate)],
    ['费用', `${escapeHtml(currency)} ${formatNumber(metrics.fees)}`], ['利息', `${escapeHtml(currency)} ${formatNumber(metrics.interest)}`],
  ];
  return `<div class="metric-grid">${values.map(([label,value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>`;
}

function equityChart(curve) {
  if (!curve?.length) return '';
  const values = curve.map(row => row.equity == null ? null : Number(row.equity));
  const samples = InvestmentLabUI.minMaxSample(values, 1200);
  const bounds = InvestmentLabUI.extent([samples.map(point => point.value)]);
  if (!bounds || !samples.length) return '';
  const range = bounds.max-bounds.min || 1;
  const points = samples.map(({index,value}) => `${(index/(Math.max(1,values.length-1))*640).toFixed(1)},${(176-(value-bounds.min)/range*156).toFixed(1)}`).join(' ');
  return `<div class="chart-wrap"><svg viewBox="0 0 640 190" role="img" aria-label="账户权益曲线"><polyline points="${points}" fill="none" stroke="#1478e8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><div class="chart-caption"><span>${escapeHtml(curve[0].date)}</span><span>${escapeHtml(curve[curve.length-1].date)}</span></div></div>`;
}

function tradeTable(trades) {
  if (!trades?.length) return '<p class="help">没有成交记录。</p>';
  return `<div class="table-wrap"><table><thead><tr><th>日期</th><th>证券</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th><th>说明</th></tr></thead><tbody>${trades.slice(0,100).map(trade => `<tr><td>${escapeHtml(trade.date)}</td><td>${escapeHtml(trade.symbol)}</td><td>${Number(trade.quantity)>0?'买入':'卖出'}</td><td>${escapeHtml(trade.quantity)}</td><td>${escapeHtml(trade.price)}</td><td>${escapeHtml(trade.fee)}</td><td>${escapeHtml(trade.reason)}</td></tr>`).join('')}</tbody></table></div>${trades.length>100?'<p class="help">仅显示前100笔成交。</p>':''}`;
}

function renderResult(result) {
  const detail = $('#result-detail');
  if (result.kind === 'single' || !result.kind) {
    const currency = result.metadata?.currency || '';
    detail.innerHTML = `${metricCards(result.metrics, currency)}${equityChart(result.curve)}${result.benchmark?`<h3>基准结果</h3>${metricCards(result.benchmark.metrics,currency)}`:''}${result.metadata?.research_warning?`<div class="result-warning">${escapeHtml(result.metadata.research_warning)}</div>`:''}${result.metadata?.notes?.length?`<div class="result-warning">${result.metadata.notes.map(escapeHtml).join('<br>')}</div>`:''}<details><summary>成交明细（最多100笔）</summary>${tradeTable(result.trades)}</details><details><summary>完整结果 JSON</summary><pre>${escapeHtml(JSON.stringify(result,null,2))}</pre></details>`;
  } else if (result.kind === 'rolling') {
    const summary = result.summary;
    detail.innerHTML = `<div class="metric-grid">${[['已完成窗口',summary.count],['跳过窗口',summary.skipped],['亏损比例',formatPercent(summary.loss_ratio)],['收益中位数',formatPercent(summary.median)],['10%分位',formatPercent(summary.q10)],['90%分位',formatPercent(summary.q90)],['最好窗口',formatPercent(summary.best)],['最差窗口',formatPercent(summary.worst)]].map(([a,b])=>`<div class="metric"><span>${escapeHtml(a)}</span><strong>${b}</strong></div>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>起点</th><th>终点</th><th>状态</th><th>收益</th><th>原因</th></tr></thead><tbody>${result.samples.map(sample=>`<tr><td>${escapeHtml(sample.start)}</td><td>${escapeHtml(sample.end||'')}</td><td>${escapeHtml(sample.status)}</td><td>${formatPercent(sample.metrics?.total_return)}</td><td>${escapeHtml(sample.reason||'')}</td></tr>`).join('')}</tbody></table></div><p class="result-warning">${escapeHtml(result.metadata?.overlap_warning||'滚动窗口研究仅供参考。')}</p>`;
  } else {
    const currency = result.development?.result?.metadata?.currency || '';
    detail.innerHTML = `<h3>开发区间</h3>${metricCards(result.development.result.metrics,currency)}${equityChart(result.development.result.curve)}<h3>留出区间</h3>${metricCards(result.holdout.result.metrics,currency)}${equityChart(result.holdout.result.curve)}<div class="result-warning">${escapeHtml(result.metadata?.initialization||'留出区间独立运行。')} ${escapeHtml(result.metadata?.parameter_selection||'')}</div>`;
  }
  detail.scrollIntoView({behavior:'smooth',block:'start'});
}

async function importPackageJson(packageJson, metadata = {}) {
  const verifiedResponse = await workerRequest('verify', {packageJson});
  const verified = verifiedResponse.value;
  const catalog = {securities:verified.securities, sessions:verified.sessions};
  const record = {id:verified.snapshot_id, name:verified.name, synthetic:verified.synthetic,
    source:verified.source, rows:verified.rows, first:catalog.sessions[0] || '',
    last:catalog.sessions.at(-1) || '', catalog, imported:new Date().toISOString(), packageJson};
  await putStore('snapshots', record);
  selectedIds = [...new Set([...selectedIds, record.id])];
  selectedSymbols = [];
  saveDraft();
  await refreshStorage();
  $('#package-status').textContent = `${record.synthetic?'合成演示':'数据快照'}已校验并保存在此浏览器：${record.name}，${record.rows.toLocaleString()} 行。`;
  if (metadata.pyodide) $('#engine-status').textContent = `固定 Pyodide ${metadata.pyodide} 已就绪；研究数据仅在本机浏览器中使用。`;
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
  $('#package-status').textContent = '正在准备五日合成演示…';
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

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

$('#export-backup').addEventListener('click', async () => {
  const button = $('#export-backup'); button.disabled = true;
  try {
    const backup = {format:'investment-lab-browser-backup', format_version:1, created:new Date().toISOString(),
      snapshots:packages.map(({id,name,synthetic,source,rows,first,last,catalog,imported,packageJson})=>({id,name,synthetic,source,rows,first,last,catalog,imported,packageJson})),
      runs};
    if (JSON.stringify(backup).length > 128 * 1024 * 1024) throw new Error('本机备份超过128 MiB，请先删除不需要的运行记录或分别保存快照。');
    downloadBlob(await gzipText(JSON.stringify(backup)), 'investment-lab-browser-backup.json.gz');
    notice('本机备份已生成到浏览器下载目录。文件不会自动上传；请自行保存在安全位置。');
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
    selectedIds = backup.snapshots.map(item => item.id);
    selectedSymbols = [];
    saveDraft();
    await refreshStorage();
    $('#package-status').textContent = `已恢复 ${backup.snapshots.length} 个快照与 ${backup.runs.length} 条本机运行记录。`;
    notice('备份已恢复到此浏览器。');
  } catch (error) { $('#package-status').textContent = `恢复失败：${error.message}`; notice(error.message, true); }
  finally { event.target.value = ''; }
});

function parseJsonField(id, label) {
  try { return JSON.parse($(`#${id}`).value); }
  catch (error) { throw new Error(`${label}不是有效 JSON：${error.message}`); }
}

function buildRunRequest() {
  const records = selectedRecords();
  if (!records.length) throw new Error('请先选择一个数据快照。');
  if (!selectedSymbols.length) throw new Error('请至少勾选一个交易标的。');
  const packagesJson = records.map(record => record.packageJson);
  const totalBytes = packagesJson.reduce((sum,value)=>sum+new Blob([value]).size,0);
  if (totalBytes > 128 * 1024 * 1024) throw new Error('本次选择的快照总量超过128 MiB，请减少标的或拆分研究。');
  const config = {
    start:$('#start').value, end:$('#end').value, symbols:selectedSymbols,
    initial_cash:$('#cash').value, max_leverage:$('#leverage').value, mode:$('#mode').value,
    commission_bps:$('#commission').value, minimum_commission:$('#minimum-commission').value,
    slippage_bps:$('#slippage').value, annual_interest:$('#interest').value,
    benchmark:$('#benchmark').value || null, cash_flows:parseJsonField('flows','资金流'),
    allow_unverified_actions:false,
  };
  const request = {run_id:crypto.randomUUID?crypto.randomUUID():String(Date.now()), packages:packagesJson,
    config, strategy:$('#strategy').value, params:parseJsonField('params','策略参数'), kind:$('#kind').value};
  if (request.kind === 'rolling') request.research = {interval:$('#interval').value, horizon:Number($('#horizon').value), end_mode:$('#end-mode').value};
  if (request.kind === 'holdout') request.research = {test_start:$('#test-start').value, gap_sessions:Number($('#gap').value)};
  return request;
}

function formatEta(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '估算剩余时间：计算中';
  if (seconds < 60) return `预计剩余 ${Math.ceil(seconds)} 秒`;
  return `预计剩余 ${Math.floor(seconds/60)} 分 ${Math.ceil(seconds%60)} 秒`;
}

$('#research-form').addEventListener('submit', async event => {
  event.preventDefault();
  const runButton = $('#run');
  let request;
  try { request = buildRunRequest(); }
  catch (error) { notice(error.message, true); return; }
  runButton.disabled = true;
  $('#progress-area').hidden = false;
  $('#progress').value = 0;
  $('#progress-text').textContent = '正在准备本机计算…';
  $('#progress-eta').textContent = '估算剩余时间：首次启动需下载引擎';
  $('#engine-status').textContent = '正在启动隔离的浏览器 Worker；首次加载会下载固定版本运行环境。';
  const progressStarted = Date.now();
  let progressRate = null;
  try {
    const response = await workerRequest('run', {request}, message => {
      const percent = message.total ? Math.min(100, message.done/message.total*100) : 0;
      const elapsed = Math.max(.001, (Date.now()-progressStarted)/1000);
      const rate = message.done > 0 ? message.done/elapsed : null;
      if (rate) progressRate = progressRate ? progressRate*.65+rate*.35 : rate;
      $('#progress').value = percent;
      $('#progress-text').textContent = `计算进度 ${message.done} / ${message.total}（${percent.toFixed(1)}%）`;
      $('#progress-eta').textContent = progressRate ? formatEta((message.total-message.done)/progressRate) : '预计剩余时间：计算中';
    });
    const result = response.value;
    const resultJson = JSON.stringify(result);
    const title = `${request.strategy} · ${request.kind} · ${request.config.start}—${request.config.end}`;
    await putStore('runs', {id:request.run_id, created:new Date().toISOString(), title, snapshotIds:selectedIds.slice(), resultJson});
    runs = (await listStore('runs')).sort((a,b)=>b.created.localeCompare(a.created));
    renderRuns();
    renderResult(result);
    $('#progress').value = 100;
    $('#progress-text').textContent = '计算完成';
    $('#progress-eta').textContent = '预计剩余时间：0 秒';
    $('#engine-status').textContent = `计算由 Pyodide ${response.pyodide} Worker 完成；结果仅保存在此浏览器。`;
    notice(`回测已完成并保存在此浏览器。结果摘要：${result.result_hash.slice(0,16)}`);
  } catch (error) {
    $('#progress-text').textContent = '计算失败';
    $('#progress-eta').textContent = error.message;
    $('#engine-status').textContent = '请检查数据覆盖、价格模式和研究参数后重试。';
    notice(error.message, true);
  } finally { runButton.disabled = selectedIds.length === 0 || selectedSymbols.length === 0; }
});

$('#kind').addEventListener('change', () => {
  $('#rolling-fields').hidden = $('#kind').value !== 'rolling';
  $('#holdout-fields').hidden = $('#kind').value !== 'holdout';
});

$('#strategy').addEventListener('change', () => {
  if ($('#strategy').value === 'monthly_equal_weight') {
    try {
      const current = JSON.parse($('#params').value);
      if (current && Object.keys(current).length === 1 && Number(current.weight) === 0.95) {
        $('#params').value = JSON.stringify({portfolio_mode:'rebalance', month_end_dates:[]});
      }
    } catch (_) { /* Keep invalid or user-edited JSON visible for correction. */ }
  }
  saveDraft();
});

for (const id of FIELD_IDS) $(`#${id}`).addEventListener('input', saveDraft);
for (const id of FIELD_IDS) $(`#${id}`).addEventListener('change', saveDraft);

async function initialize() {
  try {
    database = await openDatabase();
    restoreDraft();
    $('#kind').dispatchEvent(new Event('change'));
    await refreshStorage();
    $('#storage-label').textContent = `本机浏览器存储已就绪 · ${packages.length} 个快照 · ${runs.length} 次研究`;
  } catch (error) {
    $('#storage-label').textContent = '本机存储不可用';
    notice(`无法启动浏览器版：${error.message}`, true);
    $('#run').disabled = true;
  }
}

initialize();
