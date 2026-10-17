/* 研究记录、运行详情与结果比较。 */
const RUNS_PAGE = 50;
let runsCache = [];
let runsOffset = 0;
let runsExhausted = false;
const compareSelection = new Set();
let detailRequest = 0;
const detailState = {id: null, sample: 0, section: 'holdout', detail: null, table: 'trades', offset: 0, series: 'twr_index', benchmark: true};
const activeStatuses = ['queued', 'running'];
const terminalStatuses = ['completed', 'failed', 'cancelled', 'interrupted'];

/* Runs list */
async function loadRuns(reset = true) {
  const offset = reset ? 0 : runsOffset;
  const rows = await api(`/runs?limit=${RUNS_PAGE}&offset=${offset}`);
  runsCache = reset ? rows : [...runsCache, ...rows.filter(row => !runsCache.some(item => item.id === row.id))];
  runsOffset = offset + rows.length;
  runsExhausted = rows.length < RUNS_PAGE;
  renderRuns();
  return runsCache;
}

function runMatches(run) {
  const status = $('runs-status').value, kind = $('runs-kind').value;
  if (status === 'active' && !activeStatuses.includes(run.status)) return false;
  if (status === 'ended' && !['cancelled', 'interrupted'].includes(run.status)) return false;
  if (status && !['active', 'ended'].includes(status) && run.status !== status) return false;
  if (kind && run.kind !== kind) return false;
  const query = $('runs-search').value.trim().toLowerCase();
  if (!query) return true;
  const request = run.config || {};
  return [run.id, strategyName(request), request.strategy, ...(request.config?.symbols || [])].join(' ').toLowerCase().includes(query);
}

function runReturnCells(run) {
  const summary = run.summary || {};
  if (run.status !== 'completed') return '<td class="num">—</td><td class="num">—</td>';
  if (run.kind === 'single') {
    return `<td class="num ${InvestmentLabUI.tone(summary.total_return)}">${pct(summary.total_return)}</td><td class="num">${pct(summary.max_drawdown)}</td>`;
  }
  if (run.kind === 'rolling') {
    return `<td class="num ${InvestmentLabUI.tone(summary.median)}" title="各起点收益中位数">中位 ${pct(summary.median)}</td><td class="num" title="各起点中最差的收益">最差 ${pct(summary.worst)}</td>`;
  }
  // Holdout figures are shown only on the detail page, where each view is recorded.
  return '<td class="num" colspan="2"><small>打开详情查看</small></td>';
}

function runRow(run) {
  const request = run.config || {}, config = request.config || {};
  const id = esc(run.id), short = esc(run.id.slice(0, 10));
  const comparable = run.status === 'completed' && run.kind === 'single';
  const selected = compareSelection.has(run.id);
  const failed = run.status === 'failed';
  return `<tr class="${selected ? 'selected' : ''}">`
    + `<td class="check-col">${comparable ? `<input type="checkbox" data-compare="${id}" ${selected ? 'checked' : ''} aria-label="选择比较运行 ${short}">` : ''}</td>`
    + `<td><div class="run-cell">${InvestmentLabUI.seal(run.id, 'small')}<div><a href="#/runs/${encodeURIComponent(run.id)}">${esc(strategyName(request))}</a>`
    + `<small>${esc(Results.KIND_NAMES[run.kind] || run.kind)}，${esc(InvestmentLabUI.formatDateTime(run.created))}，${short}</small></div></div></td>`
    + `<td class="num">${esc(config.start || '—')}<small>${esc(config.end || '')}</small></td>`
    + `<td>${Results.dataBadge({synthetic: request.synthetic, reference: isReference(request)})}<small>${esc(Results.MODE_NAMES[config.mode] || config.mode || '')}</small></td>`
    + runReturnCells(run)
    + `<td>${Results.statusBadge(run.status)}${failed ? `<small class="run-error">${esc(run.summary?.message || '失败原因尚未记录，请查看详情。')}</small>` : ''}</td>`
    + `<td><div class="actions"><a class="btn small" href="#/runs/${encodeURIComponent(run.id)}">${failed ? '查看原因' : '查看'}</a>`
    + (activeStatuses.includes(run.status) ? `<button type="button" class="small danger" data-cancel="${id}">取消</button>` : '')
    + `<button type="button" class="small quiet" data-reload="${id}" title="把这次运行的设置载入策略研究">载入配置</button></div></td></tr>`;
}

function renderRuns() {
  const rows = runsCache.filter(runMatches);
  $('runs-body').innerHTML = rows.length ? rows.map(runRow).join('')
    : `<tr class="empty-row"><td colspan="8">${runsCache.length ? '没有符合筛选条件的运行。' : '还没有运行记录。到“策略研究”配置第一次回测。'}</td></tr>`;
  $('runs-count').textContent = runsCache.length ? `显示 ${rows.length} 条，已载入 ${runsCache.length} 条` : '';
  $('runs-more').hidden = runsExhausted;
  updateCompareButton();
}

function updateCompareButton() {
  const count = compareSelection.size;
  $('compare-selected').disabled = count < 2 || count > 6;
  $('compare-selected').textContent = count ? `比较所选（${count}）` : '比较所选';
}

for (const id of ['runs-status', 'runs-kind']) $(id).addEventListener('change', renderRuns);
$('runs-search').addEventListener('input', renderRuns);
$('refresh-runs').addEventListener('click', safe(() => loadRuns(true)));
$('runs-more').addEventListener('click', safe(() => loadRuns(false)));
$('compare-selected').addEventListener('click', () => go('#/compare?ids=' + [...compareSelection].map(encodeURIComponent).join(',')));
$('runs-body').addEventListener('change', event => {
  const id = event.target.dataset?.compare;
  if (!id) return;
  if (event.target.checked) {
    if (compareSelection.size >= 6) { event.target.checked = false; notice('一次最多比较 6 次运行。', true); return; }
    compareSelection.add(id);
  } else compareSelection.delete(id);
  event.target.closest?.('tr')?.classList.toggle('selected', event.target.checked);
  updateCompareButton();
});
$('runs-body').addEventListener('click', event => {
  const cancel = event.target.closest?.('[data-cancel]');
  if (cancel) return safe(async () => {
    cancel.disabled = true;
    await api(`/runs/${encodeURIComponent(cancel.dataset.cancel)}/cancel`, {});
    notice('任务已取消，记录和日志已保留。');
    stopWatching(cancel.dataset.cancel);
    await loadRuns(true);
  })();
  const reload = event.target.closest?.('[data-reload]');
  if (reload) {
    const run = runsCache.find(item => item.id === reload.dataset.reload);
    if (run) safe(() => loadRunIntoForm(run.id, run.config))();
  }
});

routeHandlers.runs = () => loadRuns(true);

/* Run detail */
function runFailureAdvice(message, referenceAvailable = false) {
  if (referenceAvailable) return '这份数据可用于“参考历史价格研究”。可按原参数使用参考数据重新运行；结果仅供参考，不含股息，不代表实际成交回测。';
  if (message.includes('仅可查阅')) return '该数据集目前仅供查阅，换策略或成交价格模式仍不能用于回测。请先补齐并验证实际交易价格、公司行动和所需成交字段；只想体验平台时，可以选择合成演示数据。';
  if (/VWAP|vwap/.test(message)) return '所选模式要求的 VWAP 数据不完整或口径未验证。请检查数据覆盖并补齐字段；合成演示可用于验证操作流程。';
  return '请按上面的原因检查配置或数据，再重新提交。失败记录和原始诊断会保留。';
}

function detailHeader(id, request, result) {
  const config = request?.config || {};
  const single = request?.kind === 'single' || !request?.kind;
  const badges = [Results.dataBadge({synthetic: request?.synthetic, reference: isReference(request)}),
    `<span class="badge">${esc(Results.MODE_NAMES[config.mode] || config.mode || '')}</span>`,
    request?.holdout_label ? `<span class="badge info">${esc(request.holdout_label)}</span>` : ''].join('');
  const actions = result ? `<button type="button" data-action="reload">载入到研究配置</button>`
    + `<details class="csv-menu"><summary>导出 CSV</summary><div class="menu-list">`
    + `<button type="button" data-export="trades">成交</button><button type="button" data-export="orders">信号与订单</button>`
    + `<button type="button" data-export="ledger">账户账本</button><button type="button" data-export="curve">每日账户曲线</button></div></details>`
    + (single && request?.kind ? `<button type="button" data-action="compare">加入比较</button>` : '') : '';
  return `<div class="detail-head"><div class="detail-title">${InvestmentLabUI.seal(id, 'large')}<div>`
    + `<h2>${esc(strategyName(request))}</h2>`
    + `<p>${esc(Results.KIND_NAMES[request?.kind] || '单次回测')}，${esc(config.start || '')} 至 ${esc(config.end || '')}，运行 ${esc(id.slice(0, 10))}${request?.created ? '，' + esc(InvestmentLabUI.formatDateTime(request.created)) : ''}</p>`
    + `<div class="detail-badges">${badges}</div></div></div>`
    + `<div class="detail-actions"><a class="btn quiet" href="#/runs">返回记录</a>${actions}</div></div>`;
}

function progressPanelHtml(id, run) {
  const p = progressInfo(run);
  return `<div class="progress" id="run-progress-panel" data-run-id="${esc(id)}"><div class="progress-label"><span id="run-progress-label">${esc(progressLabel(run))}</span>`
    + `<span id="run-progress-eta" class="muted">预计剩余：${esc(InvestmentLabUI.formatDuration(p.eta))}</span></div>`
    + `<div class="progress-track${p.valid ? '' : ' indeterminate'}" role="progressbar" aria-label="回测计算进度" aria-valuemin="0" aria-valuemax="100"${p.valid ? ` aria-valuenow="${p.percent}"` : ''}><span class="progress-fill" id="run-progress-fill"></span></div></div>`;
}

function updateProgressDisplay(id, run) {
  const panel = $('run-progress-panel');
  if (!panel || panel.dataset.runId !== id) return;
  const p = progressInfo(run), track = panel.querySelector('[role="progressbar"]');
  if ($('run-status-title') && run.status) $('run-status-title').textContent = Results.STATUS_NAMES[run.status] || run.status;
  $('run-progress-label').textContent = progressLabel(run);
  $('run-progress-eta').textContent = `预计剩余：${InvestmentLabUI.formatDuration(p.eta)}`;
  $('run-progress-fill').style.width = `${p.percent}%`;
  track.classList.toggle('indeterminate', !p.valid);
  if (p.valid) track.setAttribute('aria-valuenow', String(p.percent));
  else track.removeAttribute('aria-valuenow');
}

function statusPanel(id, d) {
  const status = d.status || (d.error ? 'failed' : 'queued');
  const terminal = ['failed', 'cancelled', 'interrupted'].includes(status), active = activeStatuses.includes(status);
  const message = d.error?.message || (terminal ? '本次运行没有保存具体原因。请保留运行编号以便排查。'
    : status === 'queued' ? '任务已排队，正在准备计算。' : '回测正在计算，进度与预计剩余时间会自动更新。');
  const referenceRetry = status === 'failed' && d.reference_research_available && !isReference(d.request) && d.request.strategy !== 'custom';
  const config = d.request.config || {};
  return `<div class="report status-panel"><h3 id="run-status-title">${esc(Results.STATUS_NAMES[status] || status)}</h3>`
    + (active ? progressPanelHtml(id, d) : `<p class="${terminal ? 'run-error' : 'help'}" role="${terminal ? 'alert' : 'status'}">${esc(message)}</p>`)
    + `<p class="help">标的：${esc((config.symbols || []).join('、'))}，${esc(config.start || '')} 至 ${esc(config.end || '')}</p>`
    + (status === 'failed' ? `<p>${esc(runFailureAdvice(message, d.reference_research_available && !isReference(d.request)))}</p>` : '')
    + `<div class="row">${referenceRetry ? '<button type="button" class="primary" id="retry-reference">按原参数使用参考数据重跑</button>' : ''}`
    + (active ? `<button type="button" class="danger" data-cancel-run="${esc(id)}">取消任务</button>` : '')
    + `<button type="button" data-action="reload">载入到研究配置</button><a class="btn" href="#/data">前往数据覆盖</a></div>`
    + Results.dataSources(d.request)
    + `<details><summary>原始诊断与运行配置</summary><pre>${esc(JSON.stringify({run_id: id, status, snapshot: d.request.snapshot, config: d.request.config, error: d.error, progress: d.progress}, null, 2))}</pre></details></div>`;
}

async function retryWithReference(request) {
  const button = $('retry-reference');
  button.disabled = true;
  try {
    const created = await api('/runs', {snapshot: request.snapshot, config: {...request.config, mode: 'reference_research'}, strategy: request.strategy,
      params: request.params, kind: request.kind, research: request.research, reference_retry_of: request.run_id});
    watchRun(created.run_id);
    go('#/runs/' + encodeURIComponent(created.run_id));
  } finally { button.disabled = false; }
}

function curveSeries(result, key) { return Results.curveChart(result, key, {benchmark: detailState.benchmark}); }

function drawDetailCharts(result) {
  InvestmentLabCharts.render($('result-chart'), curveSeries(result, detailState.series));
  const drawdownHost = $('drawdown-chart');
  if (!drawdownHost) return;
  drawdownHost.hidden = detailState.series === 'drawdown';
  if (!drawdownHost.hidden) {
    InvestmentLabCharts.render(drawdownHost, {...curveSeries(result, 'drawdown'), height: 130, legend: false, ariaLabel: '回撤'});
  }
}

function completedDetail(id, d) {
  const r = d.result, request = d.request, single = !request.kind || request.kind === 'single';
  let experiment = '';
  if (d.experiments?.samples) experiment = `<div class="report">${Results.rollingHeatmap(d.experiments, detailState.sample)}</div>`;
  else if (d.experiments?.holdout) experiment = `<div class="report">${Results.holdoutSummary(d.experiments, detailState.section)}</div>`;
  if (!r?.metrics) return experiment + '<div class="report status-panel"><p>该窗口已跳过，原因见热力图中的提示。</p></div>';
  const sectionLabel = d.experiments?.samples ? `<p class="help">当前查看：第 ${detailState.sample + 1} 个起点窗口。</p>`
    : d.experiments?.holdout ? `<p class="help">当前查看：${detailState.section === 'development' ? '开发区间' : '留出区间'}。</p>` : '';
  const seriesButtons = Results.CURVE_SERIES.map(item => `<button type="button" data-series="${item.key}" aria-pressed="${item.key === detailState.series}">${esc(item.label)}</button>`).join('');
  const metadata = r.metadata || {};
  const benchmark = r.benchmark?.metrics ? `<details><summary>基准结果（${esc(request.config?.benchmark || '')}）</summary>${Results.metrics(r.benchmark.metrics, {currency: metadata.currency})}</details>` : '';
  return experiment + `<div class="report"><section class="report-section">${sectionLabel}`
    + (metadata.reference_only ? `<p class="callout demo">${esc(metadata.research_warning)}</p>` : '')
    + (request.synthetic ? '<p class="callout demo">合成演示数据，不构成投资结论。</p>' : '')
    + Results.diagnostics(metadata)
    + Results.metrics(r.metrics, {currency: metadata.currency})
    + `<div class="chart-toolbar"><div class="segmented" role="group" aria-label="选择曲线">${seriesButtons}</div>`
    + (r.benchmark?.curve?.length ? `<label><input type="checkbox" id="show-benchmark" ${detailState.benchmark ? 'checked' : ''}>叠加基准</label>` : '') + '</div>'
    + '<div class="chart" id="result-chart"></div><div class="chart drawdown-chart" id="drawdown-chart"></div>'
    + `<p class="help">时间加权净值剔除了入金和提款的影响；入金不计为投资利润。${r.benchmark ? '基准使用相同期间与资金流。' : ''}</p>`
    + `<p class="help">${esc(metadata.assumptions || '')}${metadata.survivorship_bias ? '。' + esc(metadata.survivorship_bias) : ''}</p></section>`
    + `<section class="report-section"><div class="section-head"><h3>逐笔核对</h3><div class="segmented" role="group" aria-label="明细表">`
    + `<button type="button" data-table="trades" aria-pressed="${detailState.table === 'trades'}">成交</button>`
    + `<button type="button" data-table="orders" aria-pressed="${detailState.table === 'orders'}">信号与订单</button>`
    + `<button type="button" data-table="ledger" aria-pressed="${detailState.table === 'ledger'}">账户账本</button></div></div>`
    + `<div id="run-table"></div><div class="table-foot"><button type="button" class="small" id="table-prev">上一页</button><span id="table-page"></span><button type="button" class="small" id="table-next">下一页</button></div></section>`
    + `<section class="report-section notes">${benchmark}${Results.cashFlowPlan(metadata)}${Results.dataSources(request)}`
    + `<details><summary>复现元数据</summary><pre>${esc(JSON.stringify({run_id: id, snapshot: request.snapshot, strategy_hash: request.strategy_hash,
      engine_hash: request.engine_hash, version: request.app_version, git: request.git, metadata}, null, 2))}</pre></details></section></div>`;
}

async function openRun(id, sample = 0, section = 'holdout', reveal = true) {
  const token = ++detailRequest;
  Object.assign(detailState, {id, sample, section, offset: 0});
  const target = $('result-detail');
  if (reveal) target.innerHTML = '<div class="report status-panel" role="status">正在加载运行详情…</div>';
  let d;
  try { d = await api(`/runs/${encodeURIComponent(id)}?sample=${sample}&section=${section}`); }
  catch (error) {
    if (token !== detailRequest) return;
    target.innerHTML = `<div class="detail-head"><div class="detail-title"><h2>运行 ${esc(id.slice(0, 10))}</h2></div><div class="detail-actions"><a class="btn quiet" href="#/runs">返回记录</a></div></div>`
      + `<div class="report status-panel"><h3>无法加载运行详情</h3><p class="run-error" role="alert">${esc(error.message)}</p><button type="button" id="retry-run">重试加载</button></div>`;
    $('retry-run').onclick = safe(() => openRun(id, sample, section));
    return;
  }
  if (token !== detailRequest) return;
  detailState.detail = d;
  if (!d.result) {
    target.innerHTML = detailHeader(id, d.request, null) + statusPanel(id, d);
    if ($('retry-reference')) $('retry-reference').onclick = safe(() => retryWithReference(d.request));
    const status = d.status || 'queued';
    if (activeStatuses.includes(status)) { updateProgressDisplay(id, d); watchRun(id); }
    return;
  }
  target.innerHTML = detailHeader(id, d.request, d.result) + completedDetail(id, d);
  if (d.result.metrics) {
    drawDetailCharts(d.result);
    await loadTable();
  }
}

async function loadTable() {
  const {id, table, offset, sample, section} = detailState;
  const response = await api(`/runs/${encodeURIComponent(id)}/table?table=${table}&offset=${offset}&sample=${sample}&section=${section}`);
  if (detailState.id !== id) return;
  $('run-table').innerHTML = response.items.length ? Results.table(response.items, Results.TABLE_COLUMNS[table]) : '<p class="help">这张表没有记录。</p>';
  $('table-page').textContent = `${response.total ? offset + 1 : 0}–${Math.min(offset + 100, response.total)} / ${response.total}`;
  $('table-prev').disabled = offset === 0;
  $('table-next').disabled = offset + 100 >= response.total;
}

function detailRoute(id, {sample = detailState.sample, section = detailState.section} = {}) {
  const query = detailState.detail?.experiments?.samples ? `?sample=${sample}` : detailState.detail?.experiments?.holdout ? `?section=${section}` : '';
  return `#/runs/${encodeURIComponent(id)}${query}`;
}

$('result-detail').addEventListener('click', event => {
  const target = event.target;
  const {id, detail} = detailState;
  if (!id || !detail) return;
  const sample = target.closest?.('[data-sample]');
  if (sample) return go(detailRoute(id, {sample: Number(sample.dataset.sample)}));
  const section = target.closest?.('[data-section]');
  if (section) return go(detailRoute(id, {section: section.dataset.section}));
  const series = target.closest?.('[data-series]');
  if (series && detail.result) {
    detailState.series = series.dataset.series;
    document.querySelectorAll('[data-series]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.series === detailState.series)));
    return drawDetailCharts(detail.result);
  }
  const table = target.closest?.('[data-table]');
  if (table) {
    detailState.table = table.dataset.table;
    detailState.offset = 0;
    document.querySelectorAll('[data-table]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.table === detailState.table)));
    return safe(loadTable)();
  }
  if (target.id === 'table-prev') { detailState.offset = Math.max(0, detailState.offset - 100); return safe(loadTable)(); }
  if (target.id === 'table-next') { detailState.offset += 100; return safe(loadTable)(); }
  const exporter = target.closest?.('[data-export]');
  if (exporter) {
    exporter.closest('details').open = false;
    const name = exporter.dataset.export;
    return safe(() => downloadFromApi(`/runs/${encodeURIComponent(id)}/export?table=${name}&sample=${detailState.sample}&section=${detailState.section}`,
      `investment-lab-${id.slice(0, 10)}-${name}.csv`))();
  }
  const action = target.closest?.('[data-action]')?.dataset.action;
  if (action === 'reload') return safe(() => loadRunIntoForm(id, detail.request))();
  if (action === 'compare') {
    compareSelection.add(id);
    return go('#/compare?ids=' + [...compareSelection].slice(-6).map(encodeURIComponent).join(','));
  }
  const cancel = target.closest?.('[data-cancel-run]');
  if (cancel) return safe(async () => {
    cancel.disabled = true;
    await api(`/runs/${encodeURIComponent(id)}/cancel`, {});
    stopWatching(id);
    notice('任务已取消，记录和日志已保留。');
    await openRun(id, detailState.sample, detailState.section, false);
  })();
});
$('result-detail').addEventListener('change', event => {
  if (event.target.id !== 'show-benchmark' || !detailState.detail?.result) return;
  detailState.benchmark = event.target.checked;
  drawDetailCharts(detailState.detail.result);
});

routeHandlers.run = route => openRun(route.id, Math.max(0, Number(route.params.get('sample')) || 0),
  route.params.get('section') === 'development' ? 'development' : 'holdout', true);

jobWatch.listeners.add(async (run, terminal) => {
  const route = parseRoute();
  if (route.name === 'run' && route.id === run.id) {
    if (terminal) await openRun(run.id, detailState.sample, detailState.section, false);
    else updateProgressDisplay(run.id, run);
  }
  if (terminal && route.name === 'runs') await loadRuns(true);
});

/* Comparison of completed single runs */
async function showCompare(route) {
  const ids = [...new Set((route.params.get('ids') || '').split(',').map(id => id.trim()).filter(Boolean))].slice(0, 6);
  const host = $('compare-detail');
  // The address is the source of truth, so a shared or reloaded comparison shows the same runs.
  compareSelection.clear();
  ids.forEach(id => compareSelection.add(id));
  if (!ids.length) {
    host.innerHTML = '<div class="report"><div class="empty"><h3>还没有选择要比较的运行</h3><p>在“研究记录”里勾选 2–6 次已完成的单次回测，然后点击“比较所选”；也可以在运行详情里点击“加入比较”。</p><p><a class="btn" href="#/runs">打开研究记录</a></p></div></div>';
    return;
  }
  host.innerHTML = '<div class="report status-panel" role="status">正在读取运行结果…</div>';
  // Check kind and status first: only completed single runs are loaded, so no holdout view is recorded.
  const loaded = await Promise.all(ids.map(async id => {
    try {
      const state = await api(`/runs/${encodeURIComponent(id)}/progress`);
      if (state.status !== 'completed' || state.kind !== 'single') return {id, problem: state.kind !== 'single' ? '只比较单次回测' : '运行尚未完成'};
      return {id, detail: await api(`/runs/${encodeURIComponent(id)}`)};
    } catch (error) { return {id, problem: error.message}; }
  }));
  const runs = loaded.filter(item => item.detail?.result?.metrics);
  const skipped = loaded.filter(item => !item.detail?.result?.metrics);
  const view = Results.comparison(runs.map(item => {
    const request = item.detail.request, result = item.detail.result;
    return {id: item.id, title: strategyName(request), href: '#/runs/' + encodeURIComponent(item.id), config: request.config,
      metrics: result.metrics, currency: result.metadata?.currency || '', synthetic: !!request.synthetic, reference: isReference(request),
      curve: result.curve};
  }), {skipped, removable: true});
  host.innerHTML = `<div class="detail-head"><div class="detail-title"><div><h2>比较 ${runs.length} 次单次回测</h2><p>时间加权净值按日期叠加，剔除入金与提款影响。</p></div></div>`
    + `<div class="detail-actions"><a class="btn quiet" href="#/runs">返回记录</a><button type="button" id="clear-compare">清空比较</button></div></div>`
    + view.warnings
    + (runs.length ? `<div class="report"><section class="report-section"><div class="chart" id="compare-chart"></div></section>`
      + `<section class="report-section">${view.table}</section></div>` : '');
  if (view.chart) InvestmentLabCharts.render($('compare-chart'), view.chart);
  $('clear-compare').onclick = () => { compareSelection.clear(); go('#/compare'); };
  host.querySelectorAll('[data-uncompare]').forEach(button => button.addEventListener('click', () => {
    compareSelection.delete(button.dataset.uncompare);
    go('#/compare?ids=' + ids.filter(id => id !== button.dataset.uncompare).map(encodeURIComponent).join(','));
  }));
}

routeHandlers.compare = route => showCompare(route);
