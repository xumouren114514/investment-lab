/* 数据覆盖、标的池、Python 策略代码、备份与恢复。最后启动整个页面。 */
let instruments = [];
let openPoll = null;

/* Data coverage */
async function loadData() {
  await loadStatus();
  await loadOpenResources();
  const current = await api('/datasets');
  $('dataset-cards').innerHTML = current.length ? current.map(d => {
    const badge = d.synthetic ? '<span class="badge demo">合成演示</span>' : d.reference_research_available ? '<span class="badge demo">参考价格</span>' : '<span class="badge">真实来源</span>';
    return `<article class="dataset-card" data-filter-text="${esc([d.name, ...Object.entries(d.symbols || {}).flatMap(([symbol, s]) => [symbol, s.name, s.market, s.currency])].join(' ').toLowerCase())}">`
      + `<div class="row spread"><h3>${esc(d.name)}</h3>${badge}</div>`
      + `<p>${esc(d.first ?? '—')} 至 ${esc(d.last ?? '—')}</p><p>${num(d.rows, 0)} 条日线，${Object.keys(d.symbols || {}).length} 个标的</p>`
      + `<div class="row"><button type="button" class="small" data-coverage="${esc(d.id)}">检查覆盖与口径</button><button type="button" class="small quiet" data-export-snapshot="${esc(d.id)}">导出数据包</button></div></article>`;
  }).join('') : '<div class="report"><div class="empty"><h3>还没有数据快照</h3><p>导入此前导出的数据包，或在命令行运行 init --demo 建立合成演示数据。</p></div></div>';
  filterDatasets();
}

function filterDatasets() {
  const query = $('data-filter').value.trim().toLowerCase();
  let shown = 0, total = 0;
  document.querySelectorAll('#dataset-cards .dataset-card').forEach(card => {
    total++;
    card.hidden = !!query && !card.dataset.filterText.includes(query);
    if (!card.hidden) shown++;
  });
  $('data-count').textContent = total ? `显示 ${shown} / ${total}` : '';
}
$('data-filter').addEventListener('input', filterDatasets);

$('dataset-cards').addEventListener('click', event => {
  const coverage = event.target.closest?.('[data-coverage]');
  if (coverage) return safe(async () => {
    const report = await api('/coverage/' + encodeURIComponent(coverage.dataset.coverage));
    $('coverage-detail').innerHTML = `<div class="report"><section class="report-section"><div class="section-head"><h3>覆盖与口径</h3><button type="button" class="small quiet" id="close-coverage">收起</button></div>`
      + `<div class="table-wrap"><table><thead><tr><th>证券</th><th>实际下载区间</th><th class="num">行数</th><th>严格 VWAP</th><th>缺口与公司行动</th></tr></thead><tbody>`
      + report.items.map(item => `<tr><td>${esc(item.name)}<small>${esc(item.symbol)}，${esc(item.currency)}</small></td><td class="num">${esc(item.first)}<small>${esc(item.last)}</small></td><td class="num">${num(item.rows, 0)}</td>`
        + `<td class="num">${num(item.strict_vwap_rows, 0)} / ${num(item.rows, 0)}<small>候选均价 ${num(item.candidate_vwap_rows || 0, 0)}，停牌 ${num(item.suspended_rows || 0, 0)}</small><small>${item.synthetic ? '合成样本验证口径' : '需逐个标的核实'}</small></td>`
        + `<td>${item.missing_sessions.length} 个日期缺口<small>公司行动：${item.actions_verified ? '已标记验证' : '未验证'}，已导入现金事件 ${num(item.cash_action_count || 0, 0)}</small><small>${esc(item.execution_blocked || '')}</small></td></tr>`).join('')
      + `</tbody></table></div>`
      // C-141：不变量与覆盖率——uncovered 是"没数据可查"，不是"查过没问题"，所以要单列
      + (() => {
        const inv = report.invariants;
        if (!inv) return '';
        const row = (label, b) => `<tr><td>${label}</td><td class="num">${num(b.checked, 0)}</td>`
          + `<td class="num">${num(b.uncovered, 0)}</td>`
          + `<td class="num">${b.coverage === null ? '—' : (b.coverage * 100).toFixed(0) + '%'}</td>`
          + `<td class="num">${num(b.violation_count, 0)}</td></tr>`;
        const samples = ['suspended_zero_volume', 'preclose_consistency', 'unexplained_jumps']
          .flatMap(k => (inv[k].violations || []).slice(0, 1).map(v => `<li>${esc(v)}</li>`));
        return `<div class="table-wrap"><table><caption>数据质量不变量（未覆盖 = 没数据可查，不等于通过）</caption>`
          + '<thead><tr><th>不变量</th><th class="num">已检查</th><th class="num">未覆盖</th><th class="num">覆盖率</th><th class="num">违规</th></tr></thead><tbody>'
          + row('停牌日量额为 0', inv.suspended_zero_volume)
          + row('preclose 与前收一致（除权日豁免）', inv.preclose_consistency)
          + row('异常跳变能由公司行动解释', inv.unexplained_jumps)
          + '</tbody></table></div>'
          + (samples.length ? `<ul class="muted small">${samples.join('')}</ul>` : '')
          + `<p class="muted small">可用时刻口径：${esc(report.available_at_basis || 'trade_date')}（按交易日收盘后推的保守近似，不是真实可获得时刻）</p>`;
      })()
      + `<details><summary>来源与缺口详情</summary><pre>${esc(JSON.stringify(report, null, 2))}</pre></details></section></div>`;
    $('close-coverage').onclick = () => { $('coverage-detail').innerHTML = ''; };
    $('coverage-detail').scrollIntoView?.({block: 'start', behavior: 'smooth'});
  })();
  const exporter = event.target.closest?.('[data-export-snapshot]');
  if (exporter) return safe(async () => {
    exporter.disabled = true;
    try {
      await downloadFromApi('/exports/' + encodeURIComponent(exporter.dataset.exportSnapshot), `investment-lab-${exporter.dataset.exportSnapshot}.json.gz`);
      notice('数据包已下载到浏览器的下载目录；文件包含快照行情和来源信息，不会自动上传。分享前请确认数据许可。');
    } finally { exporter.disabled = false; }
  })();
});

$('update-data').addEventListener('click', safe(async () => {
  $('update-data').disabled = true;
  notice('正在检查交易日历、检查点和供应商配置。');
  try {
    const result = await api('/update', {});
    notice(result.message || `补数结束：新增 ${result.added}，修订 ${result.revised}，失败 ${result.failed}。`);
    await loadData();
  } finally { $('update-data').disabled = false; }
}));

async function loadOpenResources() {
  const response = await api('/open-resources'), items = response.report?.items || [], ok = items.filter(item => item.status === 'downloaded');
  const rows = ok.reduce((sum, item) => sum + (item.coverage?.rows || 0), 0), candidates = ok.reduce((sum, item) => sum + (item.coverage?.candidate_vwap_rows || 0), 0);
  $('open-progress').textContent = `${response.active ? '正在后台下载。' : ''}${ok.length} 个标的已下载，共处理 ${items.length} 个；${num(rows, 0)} 条行情，${num(candidates, 0)} 条候选均价；`
    + `${items.filter(item => item.status === 'failed').length} 个失败，${items.filter(item => item.status === 'deferred').length} 个延后。${response.report ? '详细缺口请打开数据集的覆盖报告。' : '尚未运行公开资源下载。'}`;
  $('open-samples').disabled = response.active;
  $('open-universe').disabled = response.active;
  const sources = response.catalog.resources || [];
  $('open-catalog').innerHTML = '<table><thead><tr><th>资源</th><th>本项目用途</th><th>接入状态与限制</th></tr></thead><tbody>'
    + sources.map(source => `<tr><td><a href="${esc(source.url)}" target="_blank" rel="noopener">${esc(source.name)}</a><small>${esc(source.code_license || '官方公开数据')}</small></td><td>${esc(source.use)}</td><td>${esc(source.status)}<small>${esc(source.limits)}</small></td></tr>`).join('')
    + '</tbody></table>';
  if (openPoll) { clearTimeout(openPoll); openPoll = null; }
  if (response.active) openPoll = setTimeout(safe(loadOpenResources), 3000);
}
$('open-samples').addEventListener('click', safe(async () => { await api('/open-resources', {scope: 'sample'}); notice('公开样本下载已在后台启动。'); await loadOpenResources(); }));
$('open-universe').addEventListener('click', safe(async () => { await api('/open-resources', {scope: 'universe'}); notice('完整股票池下载已在后台启动，进度逐个保存。'); await loadOpenResources(); }));
$('open-refresh').addEventListener('click', safe(loadData));

$('snapshot-import-file').addEventListener('change', () => { $('snapshot-import').disabled = !$('snapshot-import-file').files?.length; });
$('snapshot-import').addEventListener('click', async () => {
  const file = $('snapshot-import-file').files?.[0];
  if (!file) return;
  const button = $('snapshot-import'), status = $('snapshot-import-status');
  button.disabled = true;
  status.textContent = '正在解压并核对数据包…';
  status.setAttribute('aria-busy', 'true');
  try {
    const packageData = await PortableDataPackage.readGzipJson(file);
    const imported = await api('/imports', packageData);
    status.textContent = `导入完成：${imported.name}，${num(imported.rows, 0)} 条行情。新快照 ${imported.snapshot.slice(0, 12)}；原始响应不包含在数据包内。`;
    notice('数据包已校验并导入本机数据目录。');
    $('snapshot-import-file').value = '';
    await Promise.all([loadData(), loadDatasets()]);
  } catch (error) {
    status.textContent = `导入失败：${error.message}`;
    notice(error.message, true);
  } finally {
    status.removeAttribute('aria-busy');
    button.disabled = !$('snapshot-import-file').files?.length;
  }
});

routeHandlers.data = () => loadData();

/* Universe */
const KIND_LABELS = {stock: '股票', etf: 'ETF', index: '指数', future: '期货', leveraged_etf: '杠杆 ETF'};
async function loadUniverse() {
  const response = await api('/universe');
  instruments = response.instruments;
  renderUniverse();
}
function renderUniverse() {
  const market = $('universe-market').value, query = $('universe-search').value.toLowerCase();
  const rows = instruments.filter(item => (!market || item.market === market) && (!query || (item.name + item.code).toLowerCase().includes(query)));
  const stocks = instruments.filter(item => item.kind === 'stock').length;
  $('universe-note').textContent = `${instruments.length} 个标的，其中股票 ${stocks} 只。固定的事后选择股票池存在生存者偏差；保存会生成新版本，旧回测保持可追溯。`;
  $('universe-body').innerHTML = rows.length ? rows.map(item => `<tr><td><b>${esc(item.code)}</b></td><td>${esc(item.name)}</td><td>${esc(item.market)} / ${esc(item.currency)}</td>`
    + `<td>${esc(KIND_LABELS[item.kind] || item.kind)}</td><td><span class="badge${item.mapping_verified ? ' good' : ''}">${item.mapping_verified ? '已核实' : '待验证'}</span></td>`
    + `<td><button type="button" class="small" data-instrument="${esc(item.id)}">编辑${item.active ? '' : '（已停用）'}</button></td></tr>`).join('')
    : '<tr class="empty-row"><td colspan="6">没有符合条件的标的。</td></tr>';
}
$('universe-body').addEventListener('click', event => {
  const button = event.target.closest?.('[data-instrument]');
  if (!button) return;
  $('instrument-json').value = JSON.stringify(instruments.find(item => item.id === button.dataset.instrument), null, 2);
  $('instrument-editor').open = true;
  $('instrument-editor').scrollIntoView?.({behavior: 'smooth'});
});
$('universe-market').addEventListener('change', renderUniverse);
$('universe-search').addEventListener('input', renderUniverse);
$('new-instrument').addEventListener('click', () => {
  $('instrument-json').value = JSON.stringify({id: 'US:NEW', code: 'NEW', name: '新标的', market: 'US', currency: 'USD', kind: 'stock', active: true,
    effective: new Date().toISOString().slice(0, 10), listed: null, mapping_verified: false}, null, 2);
  $('instrument-editor').open = true;
  $('instrument-editor').scrollIntoView?.({behavior: 'smooth'});
});
$('save-instrument').addEventListener('click', safe(async () => {
  await api('/universe', JSON.parse($('instrument-json').value));
  notice('股票池新版本已保存，历史数据和已有回测保持可追溯。');
  await loadUniverse();
}));
routeHandlers.universe = () => loadUniverse();

/* Python strategies */
$('strategy-code').value = 'def initialize(ctx):\n    ctx.state["ordered"] = False\n\ndef on_session(ctx):\n    # ctx.history() 只返回当时可用的数据\n    if not ctx.state["ordered"]:\n        ctx.order_target_weight(ctx.symbols[0], 0.90, "初始买入")\n        ctx.state["ordered"] = True\n\ndef on_finish(result):\n    pass\n';
$('custom-files').addEventListener('change', safe(async () => {
  if (!$('custom-files').value) return;
  const response = await api('/strategy?name=' + encodeURIComponent($('custom-files').value));
  $('strategy-name').value = response.name;
  $('strategy-code').value = response.code;
}));
$('save-strategy').addEventListener('click', safe(async () => {
  await api('/strategy', {name: $('strategy-name').value, code: $('strategy-code').value});
  notice('策略已保存，可在“策略研究”的策略列表中选择运行。');
  await loadStrategies();
}));
$('strategy-code').addEventListener('keydown', event => {
  if (event.key !== 'Tab' || event.shiftKey || event.ctrlKey || event.altKey) return;
  event.preventDefault();
  const area = event.target, start = area.selectionStart;
  area.setRangeText('    ', start, area.selectionEnd, 'end');
});
routeHandlers.strategies = () => {};

/* Backups */
async function loadBackups() {
  await loadStatus();
  const response = await api('/backups');
  $('backups-body').innerHTML = response.points.length ? response.points.map(point => `<tr><td>${esc(point.id)}<small>${esc(point.created)}</small></td><td>${esc(point.app_version)}</td>`
    + `<td><span class="badge good">已完成校验</span></td><td><button type="button" class="small" data-restore="${esc(point.id)}">选择恢复点</button></td></tr>`).join('')
    : '<tr class="empty-row"><td colspan="4">还没有恢复点。点击“建立一致性备份”。</td></tr>';
  $('retention').textContent = `备份目录：${response.root}\n保留 ${response.retention.keep.length} 个恢复点，${response.retention.candidates.length} 个候选旧点；删除数量 0。`;
}
$('backups-body').addEventListener('click', event => {
  const button = event.target.closest?.('[data-restore]');
  if (!button) return;
  $('restore-id').value = button.dataset.restore;
  $('restore-destination').focus();
});
$('create-backup').addEventListener('click', safe(async () => {
  $('create-backup').disabled = true;
  try {
    const result = await api('/backups', {});
    notice(`备份已校验：${result.id}，${result.files} 个文件。`);
    await loadBackups();
  } finally { $('create-backup').disabled = false; }
}));
$('restore-backup').addEventListener('click', safe(async () => {
  if (!$('restore-id').value || !$('restore-destination').value) throw new Error('先选择恢复点，并填写一个尚不存在的独立目标目录。');
  const result = await api('/restore', {id: $('restore-id').value, destination: $('restore-destination').value});
  notice(`已恢复到 ${result.destination}；数据库检查 ${result.integrity}。当前运行目录未切换，请按操作文档完成复现与切换。`);
}));
routeHandlers.backups = () => loadBackups();

startApp();
