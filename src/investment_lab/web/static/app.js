/* 本机版核心：接口调用、提示、路由、显示偏好和后台任务状态。各页面见 view-*.js。 */
const $ = id => document.getElementById(id);
const esc = InvestmentLabUI.escapeHtml;
const pct = InvestmentLabUI.formatPercent;
const num = InvestmentLabUI.formatNumber;
const Results = InvestmentLabResults;

let labStatus = null;
let supportsMultiSnapshot = false;
let configDefaults = {};
const routeHandlers = {};

const ROUTE_TITLES = {research: '策略研究', runs: '研究记录', run: '运行详情', compare: '结果比较', data: '数据覆盖',
  universe: '标的池', strategies: 'Python 策略', backups: '备份与恢复'};

function notice(text, error = false) {
  const live = $('notice');
  if (live) live.textContent = text;
  const host = $('toasts');
  if (!host || typeof host.prepend !== 'function') return;
  const toast = document.createElement('div');
  toast.className = 'toast' + (error ? ' error' : '');
  toast.setAttribute('role', error ? 'alert' : 'status');
  toast.innerHTML = `<p>${esc(text)}</p><button type="button" aria-label="关闭提示">×</button>`;
  toast.querySelector('button').addEventListener('click', () => toast.remove());
  host.prepend(toast);
  while (host.children.length > 4) host.lastElementChild.remove();
  if (!error) setTimeout(() => toast.remove(), 8000);
}

async function api(path, body) {
  const response = await fetch('/api' + path, body === undefined ? {} : {method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Lab-Request': 'local-ui'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}

async function downloadFromApi(path, fallbackName) {
  const response = await fetch('/api' + path);
  if (!response.ok) {
    let detail = '下载失败';
    try { detail = (await response.json()).detail || detail; } catch (_) { /* Keep the generic message. */ }
    throw new Error(detail);
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] || fallbackName;
  InvestmentLabUI.downloadBlob(await response.blob(), name);
}

function safe(fn) {
  return async (...args) => {
    try { return await fn(...args); }
    catch (error) { notice(error.message, true); }
  };
}

function strategyName(request) {
  if (!request) return '—';
  if (request.strategy === 'custom') return `Python 策略 ${request.strategy_file || request.strategy_entry || ''}`.trim();
  const entry = (typeof strategyCatalog !== 'undefined' ? strategyCatalog : []).find(item => item.id === request.strategy);
  return entry?.name || request.strategy || '—';
}

function isReference(request) { return request?.config?.mode === 'reference_research'; }

function runDataLabel(request) {
  return request?.synthetic ? '合成演示' : isReference(request) ? '参考研究' : '真实来源';
}

function progressInfo(run) {
  const p = run?.progress, done = Number(p?.done), total = Number(p?.total);
  const valid = Number.isFinite(done) && Number.isFinite(total) && total > 0;
  const completed = valid ? Math.min(total, Math.max(0, done)) : 0;
  const eta = p?.eta_seconds;
  return {valid, done: completed, total, percent: valid ? Math.floor(completed / total * 100) : 0,
    eta: eta === null || eta === undefined ? null : Number(eta)};
}

function progressLabel(run) {
  const p = progressInfo(run);
  if (!p.valid) return run?.status === 'queued' ? '任务排队中，正在准备计算…' : '正在准备计算…';
  return `计算进度 ${p.done} / ${p.total}（${p.percent}%）`;
}

/* Routing: #/research, #/runs, #/runs/<id>?sample=&section=, #/compare?ids=, #/data … */
function currentHash() { return typeof location === 'undefined' ? '' : location.hash; }

function parseRoute(hash = currentHash()) {
  const [path, query = ''] = String(hash).replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(part => { try { return decodeURIComponent(part); } catch (_) { return part; } });
  const params = new URLSearchParams(query);
  if (parts[0] === 'runs' && parts[1]) return {name: 'run', id: parts[1], params};
  const name = ROUTE_TITLES[parts[0]] && parts[0] !== 'run' ? parts[0] : 'research';
  return {name, params};
}

function go(hash) {
  if (typeof location === 'undefined') return;
  if (location.hash === hash) handleRoute();
  else location.hash = hash;
}

let previousRoute = '';
function handleRoute() {
  const route = parseRoute();
  const section = route.name === 'run' ? 'runs' : route.name;
  document.querySelectorAll('.view').forEach(view => { view.hidden = view.id !== 'view-' + route.name; });
  document.querySelectorAll('.nav a').forEach(link => {
    if (link.dataset.route === section) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const title = ROUTE_TITLES[route.name];
  $('page-title').textContent = title;
  document.title = `${title}｜投资研究室`;
  if (previousRoute !== route.name && typeof window.scrollTo === 'function') window.scrollTo(0, 0);
  previousRoute = route.name;
  const handler = routeHandlers[route.name];
  if (handler) safe(handler)(route);
}

/* Appearance preferences */
function renderPreferenceButtons(prefs) {
  document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === prefs.theme)));
  document.querySelectorAll('[data-gain-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.gainChoice === prefs.gain)));
}

function initPreferences() {
  const storage = InvestmentLabUI.safeStorage(window);
  let prefs = InvestmentLabUI.readPreferences(storage);
  renderPreferenceButtons(prefs);
  const panel = $('prefs-panel'), button = $('prefs-button');
  const toggle = open => { panel.hidden = !open; button.setAttribute('aria-expanded', String(open)); };
  button.addEventListener('click', () => toggle(panel.hidden));
  panel.addEventListener('click', event => {
    const choice = event.target.closest?.('[data-theme-choice],[data-gain-choice]');
    if (!choice) return;
    prefs = {...prefs, ...(choice.dataset.themeChoice ? {theme: choice.dataset.themeChoice} : {gain: choice.dataset.gainChoice})};
    InvestmentLabUI.applyPreferences(prefs);
    InvestmentLabUI.savePreferences(storage, prefs);
    renderPreferenceButtons(prefs);
  });
  document.addEventListener('click', event => {
    if (!panel.hidden && !panel.contains(event.target) && !button.contains(event.target)) toggle(false);
    document.querySelectorAll('details.config-menu[open], details.csv-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!panel.hidden) { toggle(false); button.focus(); }
    document.querySelectorAll('details.config-menu[open], details.csv-menu[open]').forEach(menu => { menu.open = false; });
  });
}

/* Background job: the service runs one calculation at a time. */
const jobWatch = {id: null, timer: null, inFlight: false, listeners: new Set()};

function showJob(run) {
  const pill = $('job-indicator'), nav = $('nav-job');
  if (!run) { pill.hidden = true; nav.hidden = true; return; }
  const p = progressInfo(run);
  pill.hidden = false;
  nav.hidden = false;
  pill.href = '#/runs/' + encodeURIComponent(run.id);
  pill.textContent = run.status === 'queued' ? '排队中' : p.valid ? `计算中 ${p.percent}%` : '计算中';
  pill.title = `${progressLabel(run)}，预计剩余 ${InvestmentLabUI.formatDuration(p.eta)}`;
}

function stopWatching(id) {
  if (id && jobWatch.id !== id) return;
  clearInterval(jobWatch.timer);
  jobWatch.timer = null;
  jobWatch.id = null;
  showJob(null);
}

function watchRun(id) {
  if (!id) return;
  if (jobWatch.id === id && jobWatch.timer) return;
  clearInterval(jobWatch.timer);
  jobWatch.id = id;
  jobWatch.inFlight = false;
  const tick = safe(async () => {
    if (jobWatch.inFlight || jobWatch.id !== id) return;
    jobWatch.inFlight = true;
    try {
      const run = {id, ...(await api(`/runs/${encodeURIComponent(id)}/progress`))};
      if (jobWatch.id !== id) return;
      const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(run.status);
      if (terminal) stopWatching(id);
      else showJob(run);
      for (const listener of jobWatch.listeners) await listener(run, terminal);
      if (terminal) notice(`运行 ${id.slice(0, 10)} ${Results.STATUS_NAMES[run.status] || run.status}。`, run.status === 'failed');
    } finally { jobWatch.inFlight = false; }
  });
  jobWatch.timer = setInterval(tick, 1800);
  tick();
}

async function loadStatus() {
  const status = await api('/status');
  labStatus = status;
  supportsMultiSnapshot = !!status.features?.includes('multi_snapshot');
  configDefaults = status.config_defaults || {};
  $('version').textContent = `v${status.version}，数据结构 ${status.schema}`;
  $('system-state').textContent = `${status.datasets} 个数据集，其中真实来源 ${status.real_datasets} 个`;
  $('paths').textContent = `代码：${status.project_path}\n数据：${status.data_path}\nSchema：${status.schema}`;
  if (status.active_runs?.length) watchRun(status.active_runs[0]);
  if (typeof refreshConfigValidity === 'function' && typeof configReady !== 'undefined') refreshConfigValidity();
  return status;
}

function bindDocs() {
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-doc]');
    if (!button) return;
    safe(async () => {
      const doc = await api('/docs/' + button.dataset.doc);
      $('doc-title').textContent = button.textContent.trim();
      $('doc-text').textContent = doc.text;
      $('doc-dialog').showModal();
    })();
  });
  $('close-doc').onclick = () => $('doc-dialog').close();
}

function startApp() {
  initPreferences();
  bindDocs();
  window.addEventListener('hashchange', handleRoute);
  safe(async () => {
    try {
      await Promise.all([loadStatus(), loadDatasets(), loadStrategies()]);
      initializeConfigPersistence();
      updateStrategyScopeNote();
    } finally {
      handleRoute();
    }
    await loadLatestPreview();
  })();
}
