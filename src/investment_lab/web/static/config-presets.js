/* Persist form text, not parsed strategy JSON: unfinished edits must survive a retry. */
(function (root) {
  'use strict';
  const fields = ['start', 'end', 'strategy', 'kind', 'cash', 'leverage', 'mode',
    'interval', 'horizon', 'end-mode', 'test-start', 'gap', 'commission', 'slippage',
    'interest', 'warmup', 'benchmark', 'params', 'flows', 'advanced'];
  const format = 'investment-lab-research-config';
  const draftKey = 'investment-lab.research-draft.v1';
  const presetPrefix = 'investment-lab.research-preset.v1:';
  const maxBytes = 1024 * 1024;

  function validate(value) {
    if (!value || value.format !== format || value.version !== 1 || !value.fields ||
        fields.some(id => typeof value.fields[id] !== 'string') ||
        typeof value.name !== 'string' || value.name.length > 80) {
      throw new Error('配置格式或版本不支持，当前输入未替换。');
    }
    for (const key of ['snapshots', 'symbols']) {
      if (!Array.isArray(value[key]) || value[key].length > 500 ||
          value[key].some(id => typeof id !== 'string' || !id || id.length > 256) ||
          new Set(value[key]).size !== value[key].length) {
        throw new Error('配置中的快照或标的列表无效。');
      }
    }
    const strategyParams = value.strategyParams === undefined ? {} : value.strategyParams;
    if (!strategyParams || typeof strategyParams !== 'object' || Array.isArray(strategyParams) ||
        Object.keys(strategyParams).length > 100 || Object.entries(strategyParams).some(([key, params]) =>
          !key || key.length > 256 || typeof params !== 'string' || params.length > 100_000)) {
      throw new Error('配置中的分策略参数无效。');
    }
    for (const [key, params] of Object.entries(strategyParams)) {
      try {
        const parsed = JSON.parse(params);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      } catch { throw new Error(`配置中的 ${key} 策略参数不是有效 JSON 对象。`); }
    }
    // Copy only known keys; files cannot inject extra form fields or executable code.
    return {format, version: 1, name: value.name, snapshots: [...value.snapshots],
      symbols: [...value.symbols], fields: Object.fromEntries(fields.map(id => [id, value.fields[id]])),
      strategyParams: {...strategyParams}};
  }

  function encode(value) {
    const text = JSON.stringify(validate(value), null, 2);
    if (new TextEncoder().encode(text).length > maxBytes) throw new Error('配置文件不能超过 1 MB。');
    return text;
  }

  function decode(text) {
    if (new TextEncoder().encode(text).length > maxBytes) throw new Error('配置文件不能超过 1 MB。');
    let value;
    try { value = JSON.parse(text); }
    catch { throw new Error('配置文件损坏或不是有效 JSON，当前输入未替换。'); }
    return validate(value);
  }

  function capture(doc, snapshots, strategyParams = {}) {
    return validate({format, version: 1, name: doc.getElementById('preset-name').value,
      snapshots, symbols: [...doc.getElementById('symbols').selectedOptions].map(o => o.value),
      fields: Object.fromEntries(fields.map(id => [id, doc.getElementById(id).value])), strategyParams});
  }

  // One key per named preset avoids overwriting other presets when two tabs save.
  function repository(storage) {
    return {
      draft() { const raw = storage.getItem(draftKey); return raw === null ? null : decode(raw); },
      saveDraft(value) { storage.setItem(draftKey, encode(value)); },
      list() {
        const names = [];
        for (let i = 0; i < storage.length; i++) {
          const key = storage.key(i);
          if (key?.startsWith(presetPrefix)) {
            try { names.push(decodeURIComponent(key.slice(presetPrefix.length))); } catch { /* Unrelated malformed key. */ }
          }
        }
        return names.sort((a, b) => a.localeCompare(b, 'zh-CN'));
      },
      load(name) {
        const raw = storage.getItem(presetPrefix + encodeURIComponent(name));
        if (raw === null) throw new Error('这份配置已不存在，请重新选择。');
        return decode(raw);
      },
      save(name, value, overwrite = false) {
        name = name.trim();
        if (!name || name.length > 80) throw new Error('请填写 1–80 字的配置名称。');
        const key = presetPrefix + encodeURIComponent(name);
        if (!overwrite && storage.getItem(key) !== null) throw new Error('同名配置已存在，请确认覆盖或换一个名称。');
        storage.setItem(key, encode({...value, name}));
        return name;
      }
    };
  }

  const api = {fields, format, maxBytes, validate, encode, decode, capture, repository};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ResearchConfig = api;
})(typeof window === 'undefined' ? this : window);
