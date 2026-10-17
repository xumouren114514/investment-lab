/* Snapshot selection rules shared by the local app and the browser edition.
   A selection that cannot be resolved exactly is blocked; nothing is silently dropped or replaced. */
(function (root) {
  'use strict';

  const MESSAGES = {
    missing: '已保存的部分快照不在当前列表中；原编号仍保留，请检查数据或手动取消后重选。不会自动替换为新版快照。',
    empty: '请至少勾选一个数据快照。',
    calendar: '所选快照缺少交易日历，请先检查数据覆盖。',
    oldBackend: '后台仍是旧版，请重启投资研究室服务后运行。',
    synthetic: '合成演示与真实来源不能混用，请取消其中一类。',
    scope: '同一次回测只允许同一市场、币种和时区，请调整勾选。',
    reference: 'Yahoo参考价格与其他价格来源不能混合，请调整勾选。',
    overlap: '所选快照没有足够的共同覆盖区间。',
  };

  // Local API dataset → common shape.
  function fromDataset(dataset) {
    return {id: dataset.id, name: dataset.name, synthetic: !!dataset.synthetic, first: dataset.first, last: dataset.last,
      provider: dataset.source?.provider, securities: dataset.symbols || {}, rows: dataset.rows,
      referenceAvailable: !!dataset.reference_research_available, sessions: null};
  }

  // Browser IndexedDB snapshot record → common shape.
  function fromRecord(record) {
    const catalog = record.catalog || {};
    return {id: record.id, name: record.name, synthetic: !!record.synthetic, first: record.first, last: record.last,
      provider: record.source?.provider, securities: catalog.securities || {}, rows: record.rows,
      referenceAvailable: false, sessions: Array.isArray(catalog.sessions) ? catalog.sessions : null};
  }

  function commonRange(selected) {
    if (!selected.length || selected.some(item => !item.first || !item.last)) return null;
    return {first: selected.map(item => item.first).sort().at(-1), last: selected.map(item => item.last).sort()[0]};
  }

  // The first source of a symbol wins; duplicates are reported by selectionError.
  function mergeSecurities(selected) {
    const merged = {};
    for (const item of selected) {
      for (const [symbol, security] of Object.entries(item.securities || {})) {
        if (!Object.hasOwn(merged, symbol)) merged[symbol] = security;
      }
    }
    return merged;
  }

  function selectionError(selected, missing, {supportsMulti = true, formError = '', messages = {}} = {}) {
    const text = {...MESSAGES, ...messages};
    if (missing.length) return text.missing;
    if (formError) return formError;
    if (!selected.length) return text.empty;
    if (selected.some(item => !item.first || !item.last)) return text.calendar;
    if (selected.length === 1) return supportsMulti ? '' : text.oldBackend;
    if (new Set(selected.map(item => item.synthetic)).size > 1) return text.synthetic;
    const scopes = new Set(), seen = new Set(), identities = new Set();
    for (const item of selected) {
      for (const [symbol, security] of Object.entries(item.securities || {})) {
        const identity = [security.market, security.universe_id || symbol].join('/');
        if (seen.has(symbol) || identities.has(identity)) {
          return `重复标的 ${security.universe_id || symbol}：每个标的请只勾选一份行情来源。`;
        }
        seen.add(symbol);
        identities.add(identity);
        scopes.add([security.market, security.currency, security.timezone].join('/'));
      }
    }
    if (scopes.size > 1) return text.scope;
    const providers = new Set(selected.map(item => item.provider));
    if (providers.has('Yahoo/chart') && providers.size > 1) return text.reference;
    if (selected.map(item => item.first).sort().at(-1) >= selected.map(item => item.last).sort()[0]) return text.overlap;
    if (!supportsMulti) return text.oldBackend;
    return '';
  }

  function evaluate({selectedIds = [], available = [], supportsMulti = true, formError = '', messages = {}} = {}) {
    const byId = new Map(available.map(item => [item.id, item]));
    const missing = selectedIds.filter(id => !byId.has(id));
    const selected = selectedIds.map(id => byId.get(id)).filter(Boolean);
    return {
      error: selectionError(selected, missing, {supportsMulti, formError, messages}),
      // Without form-level checks: decides whether dates and symbols can be prepared.
      dataError: selectionError(selected, missing, {supportsMulti, messages}),
      missing, selected,
      securities: mergeSecurities(selected),
      range: commonRange(selected),
    };
  }

  // Index series are signal inputs only; they are never selected for trading by default.
  function tradableSymbols(securities) {
    return Object.entries(securities).filter(([, security]) => security.kind !== 'index').map(([symbol]) => symbol);
  }

  // Yahoo chart data has only split-adjusted reference prices; it can run only in reference research mode.
  function prefersReference(selected, securities) {
    return selected.length > 0 && selected.every(item => item.provider === 'Yahoo/chart') &&
      Object.values(securities).some(security => security.kind !== 'index');
  }

  // Order chosen symbols: keep the existing order, then append newly chosen ones in catalogue order.
  function orderSymbols(order, chosen, available) {
    const allowed = new Set(available);
    const picked = new Set(chosen);
    const result = order.filter(symbol => picked.has(symbol));
    for (const symbol of available) if (picked.has(symbol) && !result.includes(symbol)) result.push(symbol);
    for (const symbol of chosen) if (!allowed.has(symbol) && !result.includes(symbol)) result.push(symbol);
    return result;
  }

  const api = {MESSAGES, fromDataset, fromRecord, commonRange, mergeSecurities, selectionError, evaluate,
    tradableSymbols, prefersReference, orderSymbols};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabSelection = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
