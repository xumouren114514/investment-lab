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
let strategyCatalog = [], strategyParamsById = {}, activeStrategyId = '';
let runtimeManifest, runtimeManifestHash = '', strategySource = '';

async function sha256Hex(value) {
  const bytes = value instanceof Uint8Array ? value : new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

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
  worker = new Worker(new URL('./worker.js?v=20260924-7', import.meta.url), {type:'module'});
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
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify({fields:draftValues(), selectedIds, selectedSymbols, strategyParams:strategyParamsById})); }
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
    strategyParamsById = draft.strategyParams && typeof draft.strategyParams === 'object' && !Array.isArray(draft.strategyParams) ? draft.strategyParams : {};
  } catch (_) { /* A damaged draft falls back to safe form defaults. */ }
  activeStrategyId = $('#strategy').value;
  try { strategyParamsById[activeStrategyId] = JSON.parse($('#params').value || '{}'); } catch (_) { /* Preserve malformed JSON for correction. */ }
  renderStrategyParameters(false);
  syncMonthlyDeposit();
  updateStrategyScopeNote();
}

function strategyEntry(id=$('#strategy').value){return strategyCatalog.find(item=>item.id===id)||null;}
function parseStrategyParams(){return InvestmentLabStrategyForms.parseObject($('#params').value,'策略参数');}
function selectedStrategySymbols(){return [...selectedSymbols];}
function updateStrategyScopeNote(){
  const entry=strategyEntry();
  $('#strategy-scope-note').textContent=selectedSymbols.length>1&&entry?.scope==='single'
    ?`此策略只交易第一项：${selectedSymbols[0]}。需要同时配置多项标的时，请选择组合策略。`
    :'所选标的共用同一账户和初始资金；具体交易由所选策略决定。';
}
function renderStrategyParameters(changed=true){
  const id=$('#strategy').value,priorId=activeStrategyId;
  let prior={};try{prior=parseStrategyParams();}catch(_){/* Keep invalid JSON visible in advanced settings. */}
  if(priorId)strategyParamsById[priorId]=prior;
  let next;
  if(id===priorId)next=prior;
  else if(strategyParamsById[id])next=strategyParamsById[id];
  else if(!priorId)next=prior;
  else next=InvestmentLabStrategyForms.defaults(strategyEntry(id));
  const entry=strategyEntry(id),symbols=selectedStrategySymbols();
  if(entry)for(const field of entry.parameters||[])if(field.type==='weights'){
    const supplied=next[field.key]&&typeof next[field.key]==='object'&&!Array.isArray(next[field.key])?next[field.key]:{};
    next[field.key]=Object.fromEntries(Object.entries(supplied).filter(([symbol])=>symbols.includes(symbol)));
  }
  activeStrategyId=id;strategyParamsById[id]=next;
  $('#params').value=JSON.stringify(next,null,2);
  InvestmentLabStrategyForms.render($('#strategy-parameter-fields'),entry,next,symbols);
  $('#strategy-count').textContent=`${strategyCatalog.length} 种预设`;
  $('#strategy-parameter-status').textContent=entry?'常用字段会同步到高级 JSON；未填参数使用表单默认值。':'';
  $('#strategy-parameter-status').classList.remove('run-error');
  updateStrategyScopeNote();
  if(changed)saveDraft();
  refreshRunButton();
}
function syncStrategyParameters(){
  let current={};try{current=parseStrategyParams();}catch(error){$('#strategy-parameter-status').textContent=error.message;$('#strategy-parameter-status').classList.add('run-error');refreshRunButton();return;}
  const entry=strategyEntry(),symbols=selectedStrategySymbols();
  const rawError=InvestmentLabStrategyForms.validateValues(entry,{...InvestmentLabStrategyForms.defaults(entry),...current},symbols).error;
  if(rawError){$('#strategy-parameter-status').textContent=rawError;$('#strategy-parameter-status').classList.add('run-error');refreshRunButton();return;}
  const result=InvestmentLabStrategyForms.read($('#strategy-parameter-fields'),entry,current,symbols);
  $('#strategy-parameter-status').textContent=result.error||'参数有效；可展开高级设置查看完整 JSON。';
  $('#strategy-parameter-status').classList.toggle('run-error',!!result.error);
  if(!result.error){$('#params').value=JSON.stringify(result.value,null,2);strategyParamsById[$('#strategy').value]=result.value;saveDraft();}
  refreshRunButton();
}
function strategyParameterError(){
  try{
    const entry=strategyEntry(),raw=parseStrategyParams(),symbols=selectedStrategySymbols();
    const rawError=InvestmentLabStrategyForms.validateValues(entry,{...InvestmentLabStrategyForms.defaults(entry),...raw},symbols).error;
    return rawError||InvestmentLabStrategyForms.read($('#strategy-parameter-fields'),entry,raw,symbols).error;
  }
  catch(error){return error.message;}
}
function refreshRunButton(){
  $('#run').disabled=selectedIds.length===0||selectedSymbols.length===0||!!strategyParameterError();
}
function syncMonthlyDeposit(){
  const input=$('#monthly-deposit');
  try{
    if(document.activeElement!==input)input.value=InvestmentLabUI.readAmount($('#flows').value,'monthly','资金流');
    $('#funding-status').textContent='入金只增加现金，不会自动买入；定投金额在所选策略参数中单独设置。';
    $('#funding-status').classList.remove('run-error');input.disabled=false;
  }catch(error){$('#funding-status').textContent=error.message;$('#funding-status').classList.add('run-error');input.disabled=true;}
}
$('#strategy-parameter-fields').addEventListener('input',syncStrategyParameters);
$('#strategy-parameter-fields').addEventListener('change',syncStrategyParameters);
$('#strategy').addEventListener('change',()=>renderStrategyParameters(true));
$('#monthly-deposit').addEventListener('input',()=>{
  try{$('#flows').value=InvestmentLabUI.writeAmount($('#flows').value,'monthly',$('#monthly-deposit').value,'资金流');syncMonthlyDeposit();saveDraft();}
  catch(error){$('#funding-status').textContent=error.message;$('#funding-status').classList.add('run-error');}
});
$('#flows').addEventListener('input',syncMonthlyDeposit);
$('#params').addEventListener('input',()=>{
  try{
    const value=parseStrategyParams();strategyParamsById[$('#strategy').value]=value;
    InvestmentLabStrategyForms.render($('#strategy-parameter-fields'),strategyEntry(),value,selectedSymbols);
    const entry=strategyEntry(),check=InvestmentLabStrategyForms.validateValues(entry,{...InvestmentLabStrategyForms.defaults(entry),...value},selectedStrategySymbols());
    $('#strategy-parameter-status').textContent=check.error||'已从高级 JSON 更新参数表单。';$('#strategy-parameter-status').classList.toggle('run-error',!!check.error);
  }catch(error){$('#strategy-parameter-status').textContent=error.message;$('#strategy-parameter-status').classList.add('run-error');}
  refreshRunButton();
});
$('#strategy').addEventListener('change',()=>renderStrategyParameters(true));

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
    renderStrategyParameters(false);
    updateStrategyScopeNote();
    refreshRunButton();
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
  renderStrategyParameters(false);
  refreshRunButton();
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
  const strategy = strategyEntry();
  const rawParams = parseStrategyParams();
  const symbols = selectedStrategySymbols();
  const rawError = InvestmentLabStrategyForms.validateValues(strategy,
    {...InvestmentLabStrategyForms.defaults(strategy), ...rawParams}, symbols).error;
  if (rawError) throw new Error(rawError);
  const parameterResult = InvestmentLabStrategyForms.read($('#strategy-parameter-fields'), strategy, rawParams, symbols);
  if (parameterResult.error) throw new Error(parameterResult.error);
  strategyParamsById[$('#strategy').value] = parameterResult.value;
  $('#params').value = JSON.stringify(parameterResult.value, null, 2);
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
    config, strategy:$('#strategy').value, params:parameterResult.value, kind:$('#kind').value};
  request.engine_manifest_hash = runtimeManifestHash;
  request.strategy_source_hash = runtimeManifest.files['runtime/investment_lab/strategies/examples.py'];
  request.strategy_catalog_hash = runtimeManifest.files['runtime/investment_lab/strategies/catalog.json'];
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
    const {packages:ignoredPackages, ...frozenRequest} = request;
    frozenRequest.snapshotIds = selectedIds.slice();
    frozenRequest.strategyDefinition = strategyEntry();
    frozenRequest.strategySource = strategySource;
    await putStore('runs', {id:request.run_id, created:new Date().toISOString(), title,
      snapshotIds:selectedIds.slice(), request:frozenRequest, resultJson});
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
  } finally { refreshRunButton(); }
});

$('#kind').addEventListener('change', () => {
  $('#rolling-fields').hidden = $('#kind').value !== 'rolling';
  $('#holdout-fields').hidden = $('#kind').value !== 'holdout';
});

for (const id of FIELD_IDS) $(`#${id}`).addEventListener('input', saveDraft);
for (const id of FIELD_IDS) $(`#${id}`).addEventListener('change', saveDraft);

async function initialize() {
  try {
    const [opened] = await Promise.all([openDatabase(), loadStrategyCatalog()]);
    database = opened;
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

async function loadStrategyCatalog() {
  const [catalogResponse,manifestResponse,sourceResponse] = await Promise.all([
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
  select.innerHTML = [...groups].map(([category,items]) => `<optgroup label="${escapeHtml(category)}">${items.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('')}</optgroup>`).join('');
  if (strategyCatalog.some(item=>item.id===selected)) select.value=selected;
  else if (strategyCatalog.length) select.value=strategyCatalog[0].id;
}

initialize();
