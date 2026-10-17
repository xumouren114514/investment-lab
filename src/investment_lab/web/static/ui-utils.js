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

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatPercent(value, digits = 2) {
    const number = finite(value);
    return number === null ? '—' : `${(number * 100).toFixed(digits)}%`;
  }

  function formatNumber(value, digits = 2) {
    const number = finite(value);
    return number === null ? '—' : number.toLocaleString('zh-CN', {maximumFractionDigits: digits});
  }

  function formatMoney(value, currency = '') {
    const text = formatNumber(value, 2);
    return text === '—' || !currency ? text : `${currency} ${text}`;
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '估算中';
    const value = Number(seconds);
    if (value <= 0) return '即将完成';
    const s = Math.ceil(value);
    if (s < 60) return `${s} 秒`;
    if (s < 3600) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
    return `${Math.floor(s / 3600)} 小时 ${Math.floor(s % 3600 / 60)} 分钟`;
  }

  function formatDateTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value ?? '—') : date.toLocaleString('zh-CN', {hour12: false});
  }

  // Decimals needed to write a tick step exactly, so neighbouring axis labels never repeat.
  function stepDecimals(step) {
    const number = Number(step);
    if (!Number.isFinite(number) || number <= 0) return 0;
    const [mantissa, exponent] = Number(number.toPrecision(12)).toString().split('e-');
    const decimals = (mantissa.split('.')[1] || '').length + (exponent ? Number(exponent) : 0);
    return Math.min(10, decimals);
  }

  // Signed tone for a return-like number: positive is a gain, negative a loss.
  function tone(value) {
    const number = finite(value);
    return number === null || number === 0 ? '' : number > 0 ? 'gain' : 'loss';
  }

  function csvCell(value) {
    if (value === null || value === undefined) return '';
    let text = typeof value === 'object' ? JSON.stringify(value) : String(value);
    // Spreadsheet programs evaluate cells such as "=..." as formulas; signed numbers stay numbers.
    if (/^[=+\-@\t\r]/.test(text) && !/^[-+]?\d/.test(text)) text = `'${text}`;
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  // Columns follow first appearance across rows unless supplied explicitly.
  function toCsv(rows, columns = null) {
    const keys = columns || [];
    if (!columns) {
      const seen = new Set();
      for (const row of rows || []) for (const key of Object.keys(row || {})) if (!seen.has(key)) { seen.add(key); keys.push(key); }
    }
    const lines = [keys.map(csvCell).join(',')];
    for (const row of rows || []) lines.push(keys.map(key => csvCell(row?.[key])).join(','));
    return lines.join('\r\n') + '\r\n';
  }

  function safeFilename(name, fallback = '投资研究室') {
    const cleaned = String(name ?? '').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 120);
    return cleaned || fallback;
  }

  function downloadBlob(blob, filename, doc = root.document) {
    const url = URL.createObjectURL(blob);
    const link = doc.createElement('a');
    link.href = url;
    link.download = filename;
    doc.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function downloadCsv(text, filename, doc = root.document) {
    // The byte-order mark lets spreadsheet programs on Windows read UTF-8 Chinese text.
    downloadBlob(new Blob(['﻿' + text], {type: 'text/csv;charset=utf-8'}), filename, doc);
  }

  // FNV-1a keeps glyphs stable for an ID without needing crypto in every runtime.
  function hash32(text) {
    let value = 0x811c9dc5;
    for (const char of String(text ?? '')) {
      value ^= char.codePointAt(0);
      value = Math.imul(value, 0x01000193) >>> 0;
    }
    return value >>> 0;
  }

  // A 5x5 mirrored glyph: visually distinct per run, deterministic and escape-free.
  // `hue` lets a comparison colour the glyph like its chart series while keeping the shape.
  function seal(id, size = '', hue = null) {
    const value = hash32(id);
    const cells = [];
    for (let row = 0; row < 5; row++) {
      for (let column = 0; column < 3; column++) {
        if (value >>> (row * 3 + column) & 1) {
          cells.push([column, row]);
          if (column < 2) cells.push([4 - column, row]);
        }
      }
    }
    if (!cells.length) cells.push([2, 2]);
    const tone = Number.isInteger(hue) ? Math.abs(hue) % 6 : (value >>> 15) % 6;
    const rects = cells.map(([x, y]) => `<rect x="${x}" y="${y}" width="1" height="1" fill="currentColor"/>`).join('');
    return `<span class="seal h${tone}${size ? ' ' + size : ''}" aria-hidden="true"><svg viewBox="0 0 5 5" shape-rendering="crispEdges">${rects}</svg></span>`;
  }

  // Reading window.localStorage itself throws when the browser blocks site data.
  function safeStorage(win = root) {
    try { return win?.localStorage || null; } catch (_) { return null; }
  }

  const PREFERENCE_KEY = 'investment-lab.preferences.v1';
  function readPreferences(storage) {
    let saved = {};
    try { saved = JSON.parse(storage?.getItem(PREFERENCE_KEY) || '{}') || {}; } catch (_) { saved = {}; }
    return {
      theme: ['light', 'dark'].includes(saved.theme) ? saved.theme : 'auto',
      gain: saved.gain === 'green' ? 'green' : 'red',
    };
  }

  function applyPreferences(prefs, doc = root.document) {
    const element = doc?.documentElement;
    if (!element) return prefs;
    if (prefs.theme === 'auto') element.removeAttribute('data-theme');
    else element.setAttribute('data-theme', prefs.theme);
    if (prefs.gain === 'green') element.setAttribute('data-gain', 'green');
    else element.removeAttribute('data-gain');
    return prefs;
  }

  function savePreferences(storage, prefs) {
    try { storage?.setItem(PREFERENCE_KEY, JSON.stringify({theme: prefs.theme, gain: prefs.gain})); return true; }
    catch (_) { return false; }
  }

  function debounce(callback, wait = 150) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => callback(...args), wait);
    };
  }

  const api = {parseObject, readAmount, writeAmount, readChoice, writeChoice, snapshotCatalog, extent, minMaxSample,
    escapeHtml, finite, formatPercent, formatNumber, formatMoney, formatDuration, formatDateTime, stepDecimals, tone,
    csvCell, toCsv, safeFilename, downloadBlob, downloadCsv, hash32, seal,
    safeStorage, PREFERENCE_KEY, readPreferences, applyPreferences, savePreferences, debounce};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
