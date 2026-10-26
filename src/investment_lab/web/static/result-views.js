/* Result fragments shared by both editions. Every value from a result or request is escaped. */
(function (root) {
  'use strict';
  const UI = root.InvestmentLabUI || (typeof require === 'function' ? require('./ui-utils.js') : null);
  const esc = UI.escapeHtml;
  const pct = UI.formatPercent;
  const num = UI.formatNumber;

  const MODE_NAMES = {strict_vwap: '严格 VWAP', estimated_vwap: '估算 VWAP', close_research: '收盘价研究', reference_research: '参考历史价格研究'};
  const KIND_NAMES = {single: '单次回测', rolling: '滚动起点', holdout: '留出验证'};
  const STATUS_NAMES = {queued: '排队中', running: '计算中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断'};
  const REFERENCE_NOTE = '使用拆股调整后的参考收盘价，不代表实际成交价；忽略拆股和现金分红事件，收益不含股息，数量为参考份额，不进入正常排名。';
  const DIAGNOSTIC_ACTIONS = {skip_signal: '等待完整指标后发出信号', base_purchase: '按基础金额定投', partial_window_signal: '使用已知的短历史窗口'};

  const COLUMN_LABELS = {date: '日期', signal_date: '信号日期', symbol: '证券', side: '方向', quantity: '数量', price: '价格', fee: '费用',
    reason: '原因', visible: '可见数据摘要', type: '事件', amount: '金额', cash: '现金', debt: '借款', notional: '成交额', forced: '强平'};
  const TABLE_COLUMNS = {
    trades: ['date', 'signal_date', 'symbol', 'side', 'quantity', 'price', 'fee', 'reason', 'forced'],
    orders: ['signal_date', 'symbol', 'quantity', 'reason', 'visible'],
    ledger: ['date', 'type', 'symbol', 'amount', 'cash', 'debt', 'reason'],
  };
  const NUMERIC = new Set(['quantity', 'price', 'fee', 'amount', 'cash', 'debt', 'notional']);
  // Same leading columns as the local CSV export; the remaining fields follow in first-seen order.
  const CSV_LEADING = {
    trades: ['date', 'signal_date', 'symbol', 'quantity', 'price', 'notional', 'fee', 'reason'],
    orders: ['signal_date', 'symbol', 'quantity', 'reason'],
    ledger: ['date', 'type', 'symbol', 'amount', 'cash', 'debt', 'reason'],
    curve: ['date', 'equity', 'twr_index', 'drawdown', 'cash', 'debt', 'flow'],
  };

  function csvColumns(rows, table) {
    const list = rows || [];
    const columns = (CSV_LEADING[table] || []).filter(key => list.some(row => row && Object.hasOwn(row, key)));
    const seen = new Set(columns);
    for (const row of list) for (const key of Object.keys(row || {})) if (!seen.has(key)) { seen.add(key); columns.push(key); }
    return columns;
  }

  function statusBadge(status) {
    const tone = status === 'completed' ? 'good' : status === 'failed' ? 'bad' : ['queued', 'running'].includes(status) ? 'info live' : '';
    return `<span class="badge ${tone}">${esc(STATUS_NAMES[status] || status)}</span>`;
  }

  function dataBadge({synthetic, reference, closeResearch}) {
    if (synthetic) return '<span class="badge demo">合成演示</span>';
    if (reference) return '<span class="badge demo">参考研究</span>';
    // C-109：收盘价成交的研究模式不可与真实可成交回测比较（引擎已写 normal_ranking_eligible=false）
    if (closeResearch) return '<span class="badge demo">收盘价研究（不可比）</span>';
    return '<span class="badge">真实来源</span>';
  }

  function metric(label, value, {tone = '', note = '', lead = false, unit = ''} = {}) {
    const unitText = unit && value !== '—' ? `<em class="unit">${esc(unit)}</em>` : '';
    return `<div class="metric${tone ? ' ' + tone : ''}${lead ? ' lead' : ''}"><span>${esc(label)}</span><strong>${unitText}${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</div>`;
  }

  // Key figures first; optional statistics only when the engine produced them.
  function metrics(m = {}, {currency = ''} = {}) {
    const items = [
      metric('时间加权收益', pct(m.total_return), {tone: UI.tone(m.total_return), lead: true}),
      metric('年化收益', pct(m.annualized_return), {tone: UI.tone(m.annualized_return), note: '按日历年 365.25 复利；不足一年显示 —'}),
      metric('最大回撤', pct(m.max_drawdown), {tone: UI.tone(m.max_drawdown)}),
      metric('波动率', pct(m.volatility), {note: '日收益标准差 × √252（交易日年化）'}),
      metric('期末权益', num(m.final_equity), {unit: currency}),
      metric('净利润', num(m.net_profit), {tone: UI.tone(m.net_profit), note: '入金不计为利润', unit: currency}),
      metric('净入金', num(m.net_flows), {unit: currency}),
      metric('费用 / 融资利息', `${num(m.fees)} / ${num(m.interest)}`),
      metric('成交笔数', num(m.trades, 0)),
    ];
    if (UI.finite(m.relative_return) !== null) items.push(metric('相对基准', pct(m.relative_return),
      {tone: UI.tone(m.relative_return), note: '基准按 100% 目标仓位买入持有，整股与手续费会留少量现金'}));
    if (UI.finite(m.win_rate) !== null) items.push(metric('胜率', pct(m.win_rate),
      {note: '按平仓/交割的往返计（含双边费用）；股票为每次卖出笔次'}));
    if (UI.finite(m.money_weighted_return) !== null) items.push(metric('资金加权收益', pct(m.money_weighted_return), {tone: UI.tone(m.money_weighted_return)}));
    if (UI.finite(m.sharpe_zero_rf) !== null) items.push(metric('夏普（无风险为 0）', num(m.sharpe_zero_rf)));
    if (UI.finite(m.turnover) !== null) items.push(metric('换手倍数', num(m.turnover)));
    return `<div class="metric-grid">${items.join('')}</div>`;
  }

  function compactMetrics(m = {}, {currency = ''} = {}) {
    return `<div class="metric-grid">${[
      metric('时间加权收益', pct(m.total_return), {tone: UI.tone(m.total_return), lead: true}),
      metric('最大回撤', pct(m.max_drawdown), {tone: UI.tone(m.max_drawdown)}),
      metric('期末权益', num(m.final_equity), {unit: currency}),
      metric('费用 / 融资利息', `${num(m.fees)} / ${num(m.interest)}`),
    ].join('')}</div>`;
  }

  function diagnostics(metadata = {}) {
    const notes = Array.isArray(metadata?.notes) ? metadata.notes : [];
    const items = Array.isArray(metadata?.strategy_diagnostics) ? metadata.strategy_diagnostics.filter(item => item && typeof item === 'object') : [];
    const omitted = Math.max(0, Number(metadata?.strategy_diagnostics_omitted) || 0);
    if (!notes.length && !items.length && !omitted) return '';
    const warning = `<div class="callout demo" role="status"><strong>策略与数据提示</strong>${notes.map(note => `<p>${esc(note)}</p>`).join('')}${items.length || omitted ? `<p>保存 ${items.length} 项策略诊断${omitted ? `，另有 ${esc(omitted)} 项未保存` : ''}。请展开下方明细核对未交易或等待的原因。</p>` : ''}</div>`;
    if (!items.length) return warning;
    return warning + `<details class="strategy-diagnostics"><summary>策略诊断明细（${items.length}项）</summary><div class="table-wrap"><table><thead><tr><th>日期</th><th>策略</th><th>标的</th><th>处理方式</th><th>原因</th></tr></thead><tbody>${items.map(item => `<tr><td>${esc(item.date)}</td><td>${esc(item.strategy)}</td><td>${esc(item.symbol)}</td><td>${esc(DIAGNOSTIC_ACTIONS[item.action] ?? item.action ?? '')}</td><td>${esc(item.message ?? item.reason ?? item.code ?? '')}</td></tr>`).join('')}</tbody></table></div></details>`;
  }

  function cashFlowPlan(metadata) {
    const plan = metadata?.cash_flows;
    if (!plan || !Object.keys(plan).length) return '';
    return `<details><summary>本区间入金 / 提款计划（${Object.keys(plan).length} 个日期）</summary><p class="help">${esc(metadata.monthly_deposit_rule || '按指定交易日处理；金额为同日合计。')}</p><pre>${esc(JSON.stringify(plan, null, 2))}</pre></details>`;
  }

  function dataSources(request) {
    const sources = request.data_sources || [{snapshot: request.snapshot, name: '原始数据快照'}];
    return `<details class="data-sources"><summary>本次使用 ${sources.length} 个数据快照，共用同一账户</summary><ul>${sources.map(source => `<li>${esc(source.name)}<small>${esc(source.snapshot)}</small></li>`).join('')}</ul><p class="help">交易标的：${esc((request.config?.symbols || []).join('、'))}</p></details>`;
  }

  function cellValue(column, row) {
    if (column === 'side') {
      const quantity = Number(row.quantity);
      return Number.isFinite(quantity) && quantity !== 0 ? (quantity > 0 ? '买入' : '卖出') : '';
    }
    if (column === 'forced') return row.forced ? '是' : '';
    const value = row[column];
    return value !== null && typeof value === 'object' ? JSON.stringify(value) : value;
  }

  function table(rows, columns) {
    if (!rows?.length) return '<p class="help">没有记录。</p>';
    return `<div class="table-wrap"><table><thead><tr>${columns.map(column => `<th${NUMERIC.has(column) ? ' class="num"' : ''}>${esc(COLUMN_LABELS[column] || column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(column => {
      const value = cellValue(column, row);
      const side = column === 'side' && value ? ` class="${value === '买入' ? 'gain' : 'loss'}"` : '';
      return `<td${NUMERIC.has(column) ? ' class="num"' : side}>${esc(value ?? '')}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function rollingHeatmap(experiments, current = 0) {
    const summary = experiments.summary || {};
    return `<section class="report-section"><div class="section-head"><h3>起点热力图</h3><p>${esc(summary.count)} 个有效窗口，亏损比例 ${pct(summary.loss_ratio)}</p></div>`
      + `<p class="help">${esc(experiments.metadata?.overlap_warning || '')}。中位数 ${pct(summary.median)}，10% / 90% 分位数 ${pct(summary.q10)} / ${pct(summary.q90)}。`
      + (UI.finite(summary.window_sessions_min) !== null && summary.window_sessions_min !== summary.window_sessions_max
        ? `窗口长度 ${summary.window_sessions_min}–${summary.window_sessions_max} 个交易日不等，分位仅供参考。` : '')
      + (UI.finite(summary.relative_median) !== null ? `相对基准中位数 ${pct(summary.relative_median)}（基准与各窗口同资金流）。` : '')
      + `</p>`
      + `<div class="heatmap">${(experiments.samples || []).map((sample, index) => {
        const ok = sample.status === 'completed';
        return `<button type="button" data-sample="${index}" class="${ok ? UI.tone(sample.metrics?.total_return) : ''}"${index === current ? ' aria-current="true"' : ''} title="${esc(sample.reason || sample.end || '')}"><small>${esc(sample.start)}</small>${ok ? pct(sample.metrics?.total_return) : '跳过'}</button>`;
      }).join('')}</div></section>`;
  }

  function holdoutSummary(experiments, current = 'holdout') {
    const part = (name, label, metricsValue) => `<div class="holdout-part${current === name ? ' current' : ''}"><h4>${label}</h4>`
      + `<p>收益 <b class="num ${UI.tone(metricsValue?.total_return)}">${pct(metricsValue?.total_return)}</b>，最大回撤 <b class="num">${pct(metricsValue?.max_drawdown)}</b></p>`
      + `<button type="button" class="small" data-section="${name}"${current === name ? ' aria-pressed="true"' : ''}>查看${label}</button></div>`;
    return `<section class="report-section"><div class="section-head"><h3>开发区间与留出验证</h3><span class="badge">${esc(experiments.metadata?.label)}</span></div>`
      + `<div class="grid-2">${part('development', '开发区间', experiments.development)}${part('holdout', '留出区间', experiments.holdout)}</div>`
      + `<p class="help">${esc(experiments.metadata?.initialization)}。系统仅记录本系统内运行与查看，无法保证外部未看过数据。</p></section>`;
  }

  // Axis labels take enough decimals from the tick step that neighbouring ticks never read the same.
  const twrAxis = (value, step) => Number(value).toFixed(Math.max(2, UI.stepDecimals(step)));
  const percentAxis = (value, step) => `${(value * 100).toFixed(UI.stepDecimals(step * 100))}%`;

  // Curve series offered by the engine output; values are numbers or null.
  const CURVE_SERIES = [
    {key: 'twr_index', label: '时间加权净值', axis: twrAxis, format: value => value.toFixed(4), baseline: 1, area: true},
    {key: 'equity', label: '账户权益', money: true},
    {key: 'drawdown', label: '回撤', axis: percentAxis, format: value => pct(value), baseline: 0, area: true, className: 'sl'},
    {key: 'leverage', label: '实际杠杆', format: value => value.toFixed(3)},
    {key: 'debt', label: '借款余额', money: true},
    {key: 'margin', label: '保证金占用', money: true},
    {key: 'interest', label: '累计融资利息', money: true},
    {key: 'cash', label: '现金', money: true},
  ];

  function curveValues(curve, key) {
    return (curve || []).map(row => UI.finite(row?.[key]));
  }

  // Resolved on use, so the two editions may load charts.js before or after this file.
  const charts = () => root.InvestmentLabCharts || (typeof require === 'function' ? require('./charts.js') : null);

  // Chart options for one curve of a single result. The benchmark overlay applies to the time-weighted index only.
  function curveChart(result, key, {benchmark = true, height = 300} = {}) {
    const Charts = charts();
    const definition = CURVE_SERIES.find(item => item.key === key) || CURVE_SERIES[0];
    const currency = result?.metadata?.currency || '';
    const runs = [{label: definition.label, className: definition.className || 's0', area: !!definition.area,
      points: (result?.curve || []).map(row => ({date: row.date, value: UI.finite(row[definition.key])}))}];
    if (definition.key === 'twr_index' && benchmark && result?.benchmark?.curve?.length) {
      const weight = UI.finite(result?.benchmark?.metadata?.benchmark_weight);
      const label = weight === null ? '基准（相同期间与资金流）'
        : `基准（${(weight * 100).toFixed(0)}% 仓位买入持有，${((1 - weight) * 100).toFixed(0)}% 现金）`;
      runs.push({label, className: 'sb',
        points: result.benchmark.curve.map(row => ({date: row.date, value: UI.finite(row.twr_index)}))});
    }
    const aligned = Charts.alignByDate(runs);
    return {dates: aligned.dates, series: aligned.series, baseline: definition.baseline ?? null, height,
      axisFormat: definition.axis || Charts.compactNumber,
      format: definition.format || (definition.money ? value => UI.formatMoney(value, currency) : value => num(value, 4)),
      ariaLabel: `${definition.label}曲线`, emptyText: '这一项没有记录到可绘制的数据。'};
  }

  // Comparison of completed single runs: items are {id, title, href?, config, metrics, currency, synthetic, reference, curve}.
  // Synthetic or reference runs are never ranked, and a missing value is never marked best.
  function comparison(items, {skipped = [], removable = false} = {}) {
    const currencies = new Set(items.map(item => item.currency || ''));
    const periods = new Set(items.map(item => `${item.config?.start}/${item.config?.end}`));
    // C-109：以引擎的 normal_ranking_eligible 为准；旧结果没有该字段时退回合成/参考判断
    const unranked = items.some(item => item.synthetic || item.reference || item.rankEligible === false);
    const best = key => {
      // C-110：区间长度不同的运行比 total_return 等于让长期限必胜；这种情况不标“最优”
      if (unranked || periods.size > 1 || items.length < 2) return null;
      const values = items.map(item => UI.finite(item.metrics?.[key])).filter(value => value !== null);
      return values.length ? Math.max(...values) : null;
    };
    // Drawdowns are negative, so the largest value is the smallest drawdown.
    const bestReturn = best('total_return'), bestDrawdown = best('max_drawdown');
    const mark = (value, target) => target !== null && UI.finite(value) === target ? ' best' : '';
    const warnings = [
      periods.size > 1 ? '各运行的区间不同，曲线按日期对齐显示；区间不同，不标“最优”，比较前先核对时期与资金流。' : '',
      currencies.size > 1 ? '这些运行使用不同币种：权益不相加，收益率比较也需考虑汇率。' : '',
      unranked ? '包含合成演示、参考历史价格或收盘价研究：只作流程或参考比较，不标记最优。' : '',
      items.some(item => item.closeResearch) ? '收盘价研究按收盘价成交，不是真实可成交回测；其收益与回撤不能与严格执行结果直接比较。' : '',
      ...skipped.map(item => `运行 ${String(item.id).slice(0, 10)} 未列入：${item.problem || '没有可比较的结果'}。`),
    ].filter(Boolean);
    const link = item => item.href ? `<a href="${esc(item.href)}">${esc(item.title)}</a>`
      : `<button type="button" class="link" data-open-run="${esc(item.id)}">${esc(item.title)}</button>`;
    const rows = items.map((item, index) => {
      const m = item.metrics || {}, config = item.config || {};
      return `<tr><td><div class="run-cell">${UI.seal(item.id, 'small', index)}<div>${link(item)}<small>${esc(String(item.id).slice(0, 10))}</small></div></div></td>`
        + `<td class="num">${esc(config.start || '')}<small>${esc(config.end || '')}</small></td>`
        + `<td>${dataBadge(item)}<small>${esc(MODE_NAMES[config.mode] || '')}</small></td>`
        + `<td class="num ${UI.tone(m.total_return)}${mark(m.total_return, bestReturn)}">${pct(m.total_return)}</td>`
        + `<td class="num">${pct(m.annualized_return)}</td><td class="num${mark(m.max_drawdown, bestDrawdown)}">${pct(m.max_drawdown)}</td>`
        + `<td class="num">${pct(m.volatility)}</td><td class="num">${esc(UI.formatMoney(m.final_equity, item.currency || ''))}</td>`
        + `<td class="num">${num(m.net_flows)}</td><td class="num">${num(m.fees)}</td><td class="num">${num(m.trades, 0)}</td>`
        + (removable ? `<td><button type="button" class="small quiet" data-uncompare="${esc(item.id)}" aria-label="从比较中移除">移除</button></td>` : '')
        + '</tr>';
    }).join('');
    const Charts = charts();
    const aligned = Charts.alignByDate(items.map((item, index) => ({label: item.title, className: `s${index % 6}`,
      points: (item.curve || []).map(row => ({date: row.date, value: UI.finite(row.twr_index)}))})));
    return {
      warnings: warnings.length ? `<div class="callout warn compare-warning">${warnings.map(text => `<p>${esc(text)}</p>`).join('')}</div>` : '',
      table: items.length ? `<div class="table-wrap"><table class="compare-table"><thead><tr><th>运行</th><th>区间</th><th>数据</th><th class="num">时间加权收益</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">波动率</th><th class="num">期末权益</th><th class="num">净入金</th><th class="num">费用</th><th class="num">成交</th>${removable ? '<th></th>' : ''}</tr></thead><tbody>${rows}</tbody></table></div>`
        + '<p class="help">粗体为同组中最高收益和最小回撤（合成或参考研究不标记）。不同运行的费用、资金流和价格模式可能不同。</p>' : '',
      chart: items.length ? {dates: aligned.dates, series: aligned.series, baseline: 1, height: 340,
        axisFormat: twrAxis, format: value => value.toFixed(4), ariaLabel: '各运行的时间加权净值比较'} : null,
    };
  }

  const api = {MODE_NAMES, KIND_NAMES, STATUS_NAMES, REFERENCE_NOTE, DIAGNOSTIC_ACTIONS, COLUMN_LABELS, TABLE_COLUMNS, CURVE_SERIES,
    statusBadge, dataBadge, metric, metrics, compactMetrics, diagnostics, cashFlowPlan, dataSources, table, rollingHeatmap,
    holdoutSummary, curveValues, cellValue, curveChart, comparison, CSV_LEADING, csvColumns};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabResults = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
