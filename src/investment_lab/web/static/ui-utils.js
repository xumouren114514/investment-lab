(function (root) {
  'use strict';

  function parseObject(text, label) {
    let value;
    try { value = JSON.parse(text || '{}'); }
    catch (error) { throw new Error(`${label}不是有效 JSON：${error.message}`); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`${label}必须是 JSON 对象。`);
    }
    return value;
  }

  function readAmount(text, key, label) {
    const value = parseObject(text, label)[key];
    if (value === undefined) return '';
    const amount = String(value);
    if (!/^\d+(?:\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) {
      throw new Error(`${label}中的 ${key} 必须是大于0且最多两位小数的金额。`);
    }
    return amount;
  }

  function writeAmount(text, key, raw, label) {
    const value = parseObject(text, label);
    const amount = String(raw ?? '').trim();
    if (!amount || Number(amount) === 0) delete value[key];
    else {
      if (!/^\d+(?:\.\d{1,2})?$/.test(amount) || !Number.isFinite(Number(amount)) || Number(amount) <= 0) {
        throw new Error(`${label}金额必须大于0且最多两位小数。`);
      }
      value[key] = amount;
    }
    return JSON.stringify(value, null, 2);
  }

  function readChoice(text, key, fallback, choices, label) {
    const value = parseObject(text, label)[key] ?? fallback;
    if (!choices.includes(value)) throw new Error(`${label}中的 ${key} 当前不是可选值，请检查高级参数。`);
    return value;
  }

  function writeChoice(text, key, choice, choices, label) {
    if (!choices.includes(choice)) throw new Error(`${label}选项无效。`);
    const value = parseObject(text, label);
    value[key] = choice;
    return JSON.stringify(value, null, 2);
  }

  function snapshotCatalog(record) {
    const cached = record?.catalog;
    if (cached && typeof cached === 'object' && cached.securities && Array.isArray(cached.sessions)) {
      return {catalog:cached, migrated:false};
    }
    let manifest;
    try { manifest = JSON.parse(record?.packageJson || '{}').manifest; }
    catch (error) { throw new Error(`快照目录无法读取：${error.message}`); }
    if (!manifest || typeof manifest !== 'object') throw new Error('快照缺少行情目录。');
    const catalog = {securities:manifest.securities || {}, sessions:Array.isArray(manifest.sessions) ? manifest.sessions : []};
    record.catalog = catalog;
    return {catalog, migrated:true};
  }

  function extent(seriesList) {
    let min = Infinity, max = -Infinity;
    for (const series of seriesList) {
      for (const raw of series || []) {
        if (raw === null || raw === undefined || raw === '') continue;
        const value = Number(raw);
        if (!Number.isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
      }
    }
    return min === Infinity ? null : {min, max};
  }

  // Keep local extrema while bounding work to the chart's display resolution.
  function minMaxSample(values, maxPoints = 1200) {
    const limit = Math.max(3, Math.floor(Number(maxPoints) || 1200));
    const points = [];
    const add = index => {
      const raw = values[index];
      if (raw === null || raw === undefined || raw === '') return;
      const value = Number(raw);
      if (Number.isFinite(value)) points.push({index, value});
    };
    if (!Array.isArray(values) || !values.length) return points;
    if (values.length <= limit) {
      for (let i = 0; i < values.length; i++) add(i);
      return points;
    }
    add(0);
    const interior = values.length - 2;
    const buckets = Math.max(1, Math.floor((limit - 2) / 2));
    for (let bucket = 0; bucket < buckets; bucket++) {
      const start = 1 + Math.floor(bucket * interior / buckets);
      const end = 1 + Math.floor((bucket + 1) * interior / buckets);
      let lowIndex = -1, highIndex = -1, low = Infinity, high = -Infinity;
      for (let index = start; index < end; index++) {
        const raw = values[index];
        if (raw === null || raw === undefined || raw === '') continue;
        const value = Number(raw);
        if (!Number.isFinite(value)) continue;
        if (value < low) { low = value; lowIndex = index; }
        if (value > high) { high = value; highIndex = index; }
      }
      if (lowIndex < 0) continue;
      if (lowIndex === highIndex) add(lowIndex);
      else if (lowIndex < highIndex) { add(lowIndex); add(highIndex); }
      else { add(highIndex); add(lowIndex); }
    }
    add(values.length - 1);
    return points;
  }

  const api = {parseObject, readAmount, writeAmount, readChoice, writeChoice, snapshotCatalog, extent, minMaxSample};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
