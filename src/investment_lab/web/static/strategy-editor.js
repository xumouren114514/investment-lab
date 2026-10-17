/* Strategy parameter editing shared by both editions.
   The JSON editor text is the source of truth: unfinished JSON is kept verbatim per strategy,
   the generated form only rewrites it after a valid form edit. */
(function (root) {
  'use strict';
  const Forms = root.InvestmentLabStrategyForms || (typeof require === 'function' ? require('./strategy-forms.js') : null);

  const VALID_HINT = '常用字段会同步到高级 JSON；未填参数使用表单默认值。';
  const SYNCED_HINT = '参数有效；可展开高级设置查看完整 JSON。';

  function create({catalog, elements, symbols, onRender = () => {}, onDraft = () => {}, onValidity = () => {}}) {
    const {strategy, params, fields, status, count} = elements;
    let texts = {};
    let parsed = {};
    let activeId = '';

    const entry = (id = strategy.value) => catalog().find(item => item.id === id) || null;
    const defaults = id => Forms.defaults(entry(id));
    const parse = () => Forms.parseObject(params.value, '策略参数');

    function showStatus(message, failed) {
      status.textContent = message;
      status.classList.toggle('run-error', !!failed);
    }

    function render(changed = true) {
      const id = strategy.value, priorId = activeId;
      if (priorId) texts[priorId] = params.value;
      if (id !== priorId && priorId) {
        params.value = Object.hasOwn(texts, id) ? texts[id] : JSON.stringify(defaults(id), null, 2);
      }
      const current = entry(id), selected = symbols();
      activeId = id;
      // Before the catalogue loads the select has no value; that placeholder never owns a draft.
      if (id) texts[id] = params.value;
      let next = parsed[id] || Forms.defaults(current), error = '';
      try {
        next = parse();
        parsed[id] = next;
        error = Forms.validateValues(current, {...Forms.defaults(current), ...next}, selected).error || '';
      } catch (problem) {
        error = problem.message;
      }
      Forms.render(fields, current, next, selected);
      if (count) count.textContent = `${catalog().length} 种预设`;
      showStatus(error || (current ? VALID_HINT : ''), !!error);
      onRender();
      if (changed) onDraft();
      onValidity();
    }

    // A form edit rewrites the JSON text only when the whole form is valid.
    function sync() {
      let current;
      try { current = parse(); }
      catch (error) {
        showStatus(error.message, true);
        onValidity();
        return;
      }
      const result = Forms.read(fields, entry(), current, symbols());
      showStatus(result.error || SYNCED_HINT, !!result.error);
      if (!result.error) {
        params.value = JSON.stringify(result.value, null, 2);
        parsed[strategy.value] = result.value;
        if (strategy.value) texts[strategy.value] = params.value;
        onDraft();
      }
      onValidity();
    }

    function error() {
      try {
        const current = entry(), raw = parse(), selected = symbols();
        const rawError = Forms.validateValues(current, {...Forms.defaults(current), ...raw}, selected).error;
        return rawError || Forms.read(fields, current, raw, selected).error || '';
      } catch (problem) {
        return problem.message;
      }
    }

    // Validated parameters for a request; never reformats the editor text.
    function value() {
      const current = entry(), raw = parse(), selected = symbols();
      const rawError = Forms.validateValues(current, {...Forms.defaults(current), ...raw}, selected).error;
      if (rawError) throw new Error(rawError);
      const result = Forms.read(fields, current, raw, selected);
      if (result.error) throw new Error(result.error);
      return result.value;
    }

    // Capture the visible text before saving; returns a copy of all per-strategy drafts.
    // A select fires `input` before `change`: until the next render the editor still shows the
    // previously rendered strategy's text, so it is filed under that strategy.
    function snapshot() {
      const owner = activeId || strategy.value;
      if (owner) texts[owner] = params.value;
      const copy = {...texts};
      delete copy[''];
      return copy;
    }

    // Restore drafts; the visible editor text stays authoritative for the selected strategy.
    function restore(saved = {}) {
      texts = {...saved};
      parsed = {};
      activeId = '';
      delete texts[''];
      if (strategy.value) texts[strategy.value] = params.value;
    }

    fields.addEventListener('input', sync);
    fields.addEventListener('change', sync);

    return {entry, render, sync, error, value, snapshot, restore,
      get activeId() { return activeId; },
      resetActive() { activeId = ''; },
      get texts() { return texts; }};
  }

  const api = {create, VALID_HINT, SYNCED_HINT};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabStrategyEditor = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
