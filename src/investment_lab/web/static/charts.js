/* SVG line charts shared by the local app and the browser edition.
   Pure layout helpers are exported for tests; render() adds hover and resize behaviour. */
(function (root) {
  'use strict';
  const UI = root.InvestmentLabUI || (typeof require === 'function' ? require('./ui-utils.js') : null);
  const esc = UI.escapeHtml;

  function niceStep(span, count) {
    const raw = span / Math.max(1, count);
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / power;
    const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
    return nice * power;
  }

  function niceTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return {min: 0, max: 1, ticks: [0, 1], step: 1};
    if (min === max) {
      const pad = Math.abs(min) * .05 || 1;
      min -= pad;
      max += pad;
    }
    const step = niceStep(max - min, count);
    const low = Math.floor(min / step) * step;
    const high = Math.ceil(max / step) * step;
    const ticks = [];
    for (let i = 0; low + i * step <= high + step * 1e-9; i++) ticks.push(Number((low + i * step).toPrecision(12)));
    return {min: ticks[0], max: ticks[ticks.length - 1], ticks, step};
  }

  function indexTicks(length, count = 6) {
    if (length <= 1) return length ? [0] : [];
    const ticks = new Set();
    const slots = Math.max(2, Math.min(count, length));
    for (let i = 0; i < slots; i++) ticks.add(Math.round(i * (length - 1) / (slots - 1)));
    return [...ticks];
  }

  function dateLabel(date, longSpan) {
    const text = String(date ?? '');
    return longSpan && /^\d{4}-\d{2}/.test(text) ? text.slice(0, 7) : text;
  }

  // `step` is the axis tick step: labels get enough decimals that neighbouring ticks differ.
  function compactNumber(value, step = null) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    const abs = Math.abs(number);
    const trim = text => text.replace(/\.0+$|(\.\d*?)0+$/, '$1');
    const unit = abs >= 1e8 ? 1e8 : abs >= 1e4 ? 1e4 : 1;
    if (unit > 1) {
      const digits = Math.max(unit === 1e8 ? 2 : abs >= 1e6 ? 0 : 1, step ? UI.stepDecimals(step / unit) : 0);
      // A narrow range on a large amount would need long fractions of 万; show the plain amount instead.
      if (digits > 2) return number.toLocaleString('zh-CN', {maximumFractionDigits: UI.stepDecimals(step)});
      return trim((number / unit).toFixed(digits)) + (unit === 1e8 ? '亿' : '万');
    }
    const digits = Math.max(abs >= 100 ? 0 : abs >= 10 ? 1 : 2, step ? UI.stepDecimals(step) : 0);
    return trim(number.toFixed(digits));
  }

  function seriesExtent(series, baseline) {
    const lists = series.map(item => item.values);
    if (baseline !== null && baseline !== undefined) lists.push([baseline]);
    return UI.extent(lists);
  }

  // Returns everything needed to draw, independent of the DOM.
  function layout(options, width) {
    const height = options.height || 260;
    const dates = options.dates || [];
    const series = (options.series || []).filter(item => Array.isArray(item.values));
    const length = Math.max(dates.length, ...series.map(item => item.values.length), 0);
    const bounds = seriesExtent(series, options.baseline);
    if (!length || !bounds) return null;
    const scale = niceTicks(bounds.min, bounds.max, options.yTicks || 5);
    const axisFormat = options.axisFormat || compactNumber;
    const labels = scale.ticks.map(value => axisFormat(value, scale.step));
    const left = Math.min(96, Math.max(36, 10 + Math.max(...labels.map(label => String(label).length)) * 7));
    const pad = {left, right: 14, top: 12, bottom: 28};
    const plotWidth = Math.max(40, width - pad.left - pad.right);
    const plotHeight = Math.max(40, height - pad.top - pad.bottom);
    const x = index => pad.left + (length > 1 ? index / (length - 1) : .5) * plotWidth;
    const y = value => pad.top + (scale.max - value) / (scale.max - scale.min || 1) * plotHeight;
    const pointLimit = Math.max(200, Math.floor(plotWidth * 2));
    const paths = series.map((item, order) => {
      const points = UI.minMaxSample(item.values, pointLimit);
      const line = points.map((point, i) => `${i ? 'L' : 'M'}${x(point.index).toFixed(1)} ${y(point.value).toFixed(1)}`).join('');
      let area = '';
      if (item.area && points.length) {
        const floor = options.baseline !== null && options.baseline !== undefined ? y(options.baseline) : pad.top + plotHeight;
        area = `${line}L${x(points[points.length - 1].index).toFixed(1)} ${floor.toFixed(1)}L${x(points[0].index).toFixed(1)} ${floor.toFixed(1)}Z`;
      }
      return {line, area, className: item.className || `s${order % 6}`};
    });
    const longSpan = dates.length > 260;
    const xTicks = indexTicks(dates.length, width < 520 ? 3 : 6).map(index => ({index, x: x(index), label: dateLabel(dates[index], longSpan)}));
    const yTicks = scale.ticks.map((value, i) => ({value, y: y(value), label: labels[i]}));
    return {width, height, pad, plotWidth, plotHeight, length, x, y, paths, xTicks, yTicks, scale, series, dates};
  }

  function svgMarkup(options, width) {
    const plan = layout(options, width);
    if (!plan) return '';
    const {pad, plotWidth, plotHeight, height} = plan;
    const baseline = options.baseline;
    const grid = plan.yTicks.map(tick =>
      `<line x1="${pad.left}" x2="${(pad.left + plotWidth).toFixed(1)}" y1="${tick.y.toFixed(1)}" y2="${tick.y.toFixed(1)}"${baseline !== null && baseline !== undefined && Math.abs(tick.value - baseline) < 1e-12 ? ' class="zero"' : ''}/>`).join('');
    const yLabels = plan.yTicks.map(tick =>
      `<text x="${pad.left - 8}" y="${(tick.y + 4).toFixed(1)}" text-anchor="end">${esc(tick.label)}</text>`).join('');
    const xLabels = plan.xTicks.map((tick, i) => {
      const anchor = plan.xTicks.length > 1 && i === 0 ? 'start' : i === plan.xTicks.length - 1 && plan.xTicks.length > 1 ? 'end' : 'middle';
      return `<text x="${tick.x.toFixed(1)}" y="${(pad.top + plotHeight + 19).toFixed(1)}" text-anchor="${anchor}">${esc(tick.label)}</text>`;
    }).join('');
    const areas = plan.paths.filter(path => path.area).map(path => `<path class="area ${path.className}" d="${path.area}"/>`).join('');
    const lines = plan.paths.map(path => `<path class="series ${path.className}" d="${path.line}"/>`).join('');
    const label = esc(options.ariaLabel || '时间序列图');
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${label}" tabindex="0">`
      + `<g class="grid">${grid}</g><g class="axis">${yLabels}${xLabels}</g>${areas}${lines}`
      + `<g class="hover" visibility="hidden"><line class="cross" x1="0" x2="0" y1="${pad.top}" y2="${(pad.top + plotHeight).toFixed(1)}"/></g>`
      + `<rect class="hit" x="${pad.left}" y="${pad.top}" width="${plotWidth.toFixed(1)}" height="${plotHeight.toFixed(1)}"/></svg>`;
  }

  function legendMarkup(series) {
    return `<ul class="chart-legend">${series.map((item, order) =>
      `<li class="${esc(item.className || `s${order % 6}`)}"><i></i><span>${esc(item.label || '')}</span></li>`).join('')}</ul>`;
  }

  function tooltipMarkup(options, index) {
    const format = options.format || (value => UI.formatNumber(value, 4));
    const rows = (options.series || []).map((item, order) => {
      const value = item.values[index];
      if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '';
      return `<span class="${esc(item.className || `s${order % 6}`)}"><i></i>${esc(item.label || '')}<em>${esc(format(Number(value), item))}</em></span>`;
    }).join('');
    return `<b>${esc(options.dates?.[index] ?? '')}</b>${rows}`;
  }

  const SVG = 'http://www.w3.org/2000/svg';
  const charts = new WeakMap();

  function render(host, options) {
    if (!host) return null;
    const previous = charts.get(host);
    if (previous) previous.destroy();
    const state = {options, width: 0, index: null, observer: null, frame: 0};
    const width = () => Math.max(260, Math.floor(host.clientWidth || host.getBoundingClientRect?.().width || 640));

    function draw() {
      state.width = width();
      const markup = svgMarkup(state.options, state.width);
      if (!markup) {
        host.innerHTML = `<p class="help">${esc(state.options.emptyText || '没有可绘制的数据。')}</p>`;
        return;
      }
      host.innerHTML = markup + '<div class="chart-tip" hidden></div>' + (state.options.legend === false ? '' : legendMarkup(state.options.series || []));
      bind();
    }

    function bind() {
      const svg = host.querySelector('svg');
      const tip = host.querySelector('.chart-tip');
      if (!svg || !tip || !svg.addEventListener) return;
      const plan = layout(state.options, state.width);
      const hover = svg.querySelector('.hover');
      const cross = svg.querySelector('.cross');
      const show = (index, pointerY = null) => {
        state.index = Math.max(0, Math.min(plan.length - 1, index));
        const px = plan.x(state.index);
        cross.setAttribute('x1', px.toFixed(1));
        cross.setAttribute('x2', px.toFixed(1));
        hover.querySelectorAll('circle').forEach(dot => dot.remove());
        (state.options.series || []).forEach((item, order) => {
          const value = Number(item.values[state.index]);
          if (item.values[state.index] === null || item.values[state.index] === undefined || !Number.isFinite(value)) return;
          const dot = document.createElementNS(SVG, 'circle');
          dot.setAttribute('cx', px.toFixed(1));
          dot.setAttribute('cy', plan.y(value).toFixed(1));
          dot.setAttribute('r', '3.5');
          dot.setAttribute('class', `dot ${item.className || `s${order % 6}`}`);
          hover.appendChild(dot);
        });
        hover.setAttribute('visibility', 'visible');
        tip.innerHTML = tooltipMarkup(state.options, state.index);
        tip.hidden = false;
        const box = svg.getBoundingClientRect();
        const ratio = box.width / state.width || 1;
        const tipWidth = tip.offsetWidth || 160, tipHeight = tip.offsetHeight || 60;
        let left = px * ratio + 12;
        if (left + tipWidth > box.width) left = Math.max(0, px * ratio - tipWidth - 12);
        // Follow the pointer vertically so the reading stays in view on long pages.
        const anchor = pointerY ?? plan.pad.top * ratio;
        const top = Math.max(0, Math.min(box.height - tipHeight, anchor - tipHeight / 2));
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
      };
      const hide = () => {
        hover.setAttribute('visibility', 'hidden');
        tip.hidden = true;
        state.index = null;
      };
      svg.addEventListener('pointermove', event => {
        const box = svg.getBoundingClientRect();
        const localX = (event.clientX - box.left) * (state.width / (box.width || state.width));
        const ratio = (localX - plan.pad.left) / plan.plotWidth;
        if (ratio < -.02 || ratio > 1.02) return hide();
        show(Math.round(Math.max(0, Math.min(1, ratio)) * (plan.length - 1)), event.clientY - box.top);
      });
      svg.addEventListener('pointerleave', hide);
      svg.addEventListener('blur', hide);
      svg.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const step = event.shiftKey ? Math.max(1, Math.round(plan.length / 20)) : 1;
        const current = state.index ?? (event.key === 'ArrowLeft' ? plan.length : -1);
        show(event.key === 'Home' ? 0 : event.key === 'End' ? plan.length - 1 : current + (event.key === 'ArrowLeft' ? -step : step));
      });
    }

    const controller = {
      update(next) { state.options = {...state.options, ...next}; draw(); },
      destroy() {
        state.observer?.disconnect();
        if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(state.frame);
        charts.delete(host);
      },
    };
    charts.set(host, controller);
    draw();
    if (typeof ResizeObserver === 'function' && typeof requestAnimationFrame === 'function') {
      state.observer = new ResizeObserver(() => {
        const next = width();
        if (Math.abs(next - state.width) < 8 || host.isConnected === false) return;
        cancelAnimationFrame(state.frame);
        state.frame = requestAnimationFrame(draw);
      });
      state.observer.observe(host);
    }
    return controller;
  }

  // Align several runs on one date axis; values missing on a date stay empty.
  function alignByDate(runs) {
    const dates = [...new Set(runs.flatMap(run => run.points.map(point => point.date)))].sort();
    const position = new Map(dates.map((date, index) => [date, index]));
    const series = runs.map(run => {
      const values = new Array(dates.length).fill(null);
      for (const point of run.points) values[position.get(point.date)] = point.value;
      return {...run, values};
    });
    return {dates, series};
  }

  const api = {niceStep, niceTicks, indexTicks, compactNumber, layout, svgMarkup, legendMarkup, tooltipMarkup, render, alignByDate};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestmentLabCharts = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
