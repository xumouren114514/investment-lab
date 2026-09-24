(function (root) {
  'use strict';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const parseObject = (value, label = '策略参数') => {
    let parsed;
    try { parsed = JSON.parse(value || '{}'); }
    catch (error) { throw new Error(`${label}不是有效 JSON：${error.message}`); }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象。`);
    return parsed;
  };

  function defaults(entry) {
    return Object.fromEntries((entry?.parameters || []).filter(field => field.default !== undefined)
      .map(field => [field.key, field.default]));
  }

  function renderField(field, values, symbols) {
    const value = values[field.key] ?? field.default;
    const description = field.help ? `<span class="help">${esc(field.help)}</span>` : '';
    if (field.type === 'select') {
      return `<label>${esc(field.label)}<select data-strategy-param="${esc(field.key)}" data-param-type="select">${field.options.map(option => `<option value="${esc(option.value)}" ${option.value === value ? 'selected' : ''}>${esc(option.label)}</option>`).join('')}</select>${description}</label>`;
    }
    if (field.type === 'weights') {
      const supplied = value && typeof value === 'object' ? value : {};
      const equal = symbols.length ? 1 / symbols.length : 0;
      const rows = symbols.map(symbol => `<label class="weight-row">${esc(symbol)}<input type="number" min="0" max="1" step="0.01" data-strategy-param="${esc(field.key)}" data-param-type="weights" data-symbol="${esc(symbol)}" value="${esc(supplied[symbol] ?? equal.toFixed(2))}"></label>`).join('');
      return `<fieldset class="strategy-weight-field"><legend>${esc(field.label)}</legend><div class="strategy-weight-list">${rows || '<span class="help">先选择至少一个可交易标的。</span>'}</div>${description}</fieldset>`;
    }
    if (field.type === 'date_list') {
      const dates = Array.isArray(value) ? value.join(', ') : '';
      return `<label>${esc(field.label)}<input type="text" data-strategy-param="${esc(field.key)}" data-param-type="date_list" value="${esc(dates)}" placeholder="YYYY-MM-DD, YYYY-MM-DD">${description}</label>`;
    }
    if (field.type === 'json') {
      return `<label>${esc(field.label)}<textarea data-strategy-param="${esc(field.key)}" data-param-type="json" rows="3">${esc(JSON.stringify(value ?? {}, null, 2))}</textarea>${description}</label>`;
    }
    const step = stepFor(field);
    return `<label>${esc(field.label)}<input type="number" data-strategy-param="${esc(field.key)}" data-param-type="${esc(field.type)}" value="${esc(value)}"${field.min !== undefined ? ` min="${esc(field.min)}"` : ''}${field.max !== undefined ? ` max="${esc(field.max)}"` : ''} step="${esc(step)}">${description}</label>`;
  }

  function stepFor(field) {
    if (field.type === 'integer') return 1;
    const increment = Number(field.step);
    // Large increments are spinner hints, not cent-precision validation steps.
    return Number.isFinite(increment) && increment > 0 && increment <= 1 ? field.step : 'any';
  }

  function render(host, entry, values, symbols = []) {
    if (!host) return;
    if (!entry) {
      host.innerHTML = '<p class="help">该 Python 策略的参数由策略代码定义，可在高级设置中填写 JSON。</p>';
      return;
    }
    const fields = entry.parameters || [];
    host.innerHTML = `<div class="strategy-description"><strong>${esc(entry.category || '预设策略')}</strong><span>${esc(entry.description || '')}</span></div>${fields.length ? `<div class="strategy-fields">${fields.map(field => renderField(field, values || {}, symbols)).join('')}</div>` : '<p class="help">此策略无需额外参数。</p>'}<p class="help strategy-timing-note">信号只使用当前及以前可用的历史数据；订单按下一交易日模拟执行。观察窗口不足或必要字段缺失时会跳过信号，不会自动替换价格口径。</p>`;
  }

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
  }

  function validateValues(entry, values = {}, symbols = []) {
    const result = {...values};
    if (!entry) return {value:result, error:null};
    try {
      for (const field of entry.parameters || []) {
        const value = result[field.key] ?? field.default;
        if (value === undefined) throw new Error(`请填写${field.label}。`);
        if (field.type === 'number' || field.type === 'integer') {
          const numeric = Number(value);
          if (!Number.isFinite(numeric)) throw new Error(`${field.label}必须是有效数字。`);
          if (field.type === 'integer' && !Number.isInteger(numeric)) throw new Error(`${field.label}必须是整数。`);
          if (field.min !== undefined && numeric < field.min) throw new Error(`${field.label}不能小于 ${field.min}。`);
          if (field.max !== undefined && numeric > field.max) throw new Error(`${field.label}不能大于 ${field.max}。`);
          result[field.key] = numeric;
        } else if (field.type === 'select') {
          if (!field.options.some(option => option.value === value)) throw new Error(`${field.label}不是可选值。`);
        } else if (field.type === 'date_list') {
          if (!Array.isArray(value) || value.some(day => typeof day !== 'string' || !validDate(day))) throw new Error(`${field.label}请使用有效的 YYYY-MM-DD 日期。`);
          if (new Set(value).size !== value.length) throw new Error(`${field.label}不能包含重复日期。`);
        } else if (field.type === 'json') {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${field.label}必须是 JSON 对象。`);
        } else if (field.type === 'weights') {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${field.label}必须是标的与比例的映射。`);
          let total = 0;
          for (const [symbol, weight] of Object.entries(value)) {
            const numeric = Number(weight);
            if ((symbols.length && !symbols.includes(symbol)) || !Number.isFinite(numeric) || numeric < 0 || numeric > 1) throw new Error(`${symbol} 的目标比例无效，或标的未被选中。`);
            total += numeric;
          }
          if (total > Number(field.maxTotal ?? 1) + 1e-8) throw new Error('目标比例总和不能超过100%。');
        }
        result[field.key] = value;
      }
      const rules = [
        [entry.id === 'ema_crossover' || entry.id === 'macd', result.fast_window >= result.slow_window, '快线窗口必须小于慢线窗口。'],
        [entry.id === 'donchian_breakout', result.exit_window > result.entry_window, '退出窗口不能大于入场窗口。'],
        [entry.id === 'rsi_mean_reversion' || entry.id === 'stochastic_mean_reversion', result.entry >= result.exit, '入场线必须低于退出线。'],
        [entry.id === 'rotation' && symbols.length > 0, result.top_n > symbols.length, '领先标的数量不能超过已选择的交易标的数量。'],
      ];
      const failed = rules.find(([applies, invalid]) => applies && invalid);
      if (failed) throw new Error(failed[2]);
      return {value:result, error:null};
    } catch (error) {
      return {value:result, error:error.message};
    }
  }

  function read(host, entry, previous = {}, symbols = []) {
    const result = {...previous};
    if (!entry) return {value:result, error:null};
    try {
      for (const field of entry.parameters || []) {
        const inputs = [...host.querySelectorAll('[data-strategy-param]')].filter(input => input.dataset.strategyParam === field.key);
        if (field.type === 'weights') {
          const weights = {};
          for (const input of inputs) {
            if (input.value === '') continue;
            const value = Number(input.value);
            if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error(`${input.dataset.symbol} 的目标比例必须在0到1之间。`);
            weights[input.dataset.symbol] = value;
          }
          const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
          if (total > Number(field.maxTotal ?? 1) + 1e-8) throw new Error('目标比例总和不能超过100%。');
          result[field.key] = weights;
        } else if (field.type === 'date_list') {
          const raw = inputs[0]?.value || '';
          const dates = raw.split(/[\s,，;；]+/).filter(Boolean);
          if (dates.some(day => !validDate(day))) throw new Error(`${field.label}请使用有效的 YYYY-MM-DD 日期。`);
          if (new Set(dates).size !== dates.length) throw new Error(`${field.label}不能包含重复日期。`);
          result[field.key] = dates;
        } else if (field.type === 'json') {
          result[field.key] = parseObject(inputs[0]?.value || '{}', field.label);
        } else {
          const input = inputs[0];
          const raw = input?.value ?? '';
          if (raw === '') throw new Error(`请填写${field.label}。`);
          if (field.type === 'select') result[field.key] = raw;
          else {
            const value = Number(raw);
            if (!Number.isFinite(value)) throw new Error(`${field.label}必须是有效数字。`);
            if (field.type === 'integer' && !Number.isInteger(value)) throw new Error(`${field.label}必须是整数。`);
            if (field.min !== undefined && value < field.min) throw new Error(`${field.label}不能小于 ${field.min}。`);
            if (field.max !== undefined && value > field.max) throw new Error(`${field.label}不能大于 ${field.max}。`);
            result[field.key] = value;
          }
        }
      }
      return validateValues(entry, result, symbols);
    } catch (error) {
      return {value:result, error:error.message};
    }
  }

  const api = {defaults, parseObject, render, read, stepFor, validateValues};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.InvestmentLabStrategyForms = api;
})(typeof window === 'undefined' ? globalThis : window);
