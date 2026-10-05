const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Config = require('../src/investment_lab/web/static/config-presets.js');
const Forms = require('../src/investment_lab/web/static/strategy-forms.js');
const UI = require('../src/investment_lab/web/static/ui-utils.js');
const root = path.resolve(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'src/investment_lab/strategies/catalog.json'), 'utf8'));

function storage() {
  const data = new Map();
  return {get length() { return data.size; }, key: i => [...data.keys()][i],
    getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value))};
}
const decode = value => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
function attributes(text) {
  return Object.fromEntries([...text.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(match => [match[1], decode(match[2] || '')]));
}

// A deliberately small isolated DOM, with real event listeners, select options,
// generated strategy controls and in-memory storage. Load the complete page
// script, rather than copying its restore/render functions into the test.
class Element {
  constructor(id = '', tag = 'input') {
    this.id = id; this.tagName = tag.toUpperCase(); this.dataset = {}; this.options = [];
    this.children = []; this.controls = []; this.listeners = new Map(); this._value = '';
    this.textContent = ''; this.hidden = false; this.disabled = false; this.checked = false;
    this.classes = new Set();
    this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name),
      toggle: (name, on) => { if (on) this.classes.add(name); else this.classes.delete(name); }};
  }
  get value() { return this.tagName === 'SELECT' ? (this.options.find(option => option.selected)?.value || '') : this._value; }
  set value(value) {
    this._value = String(value);
    if (this.tagName === 'SELECT') this.options.forEach(option => { option.selected = option.value === this._value; });
  }
  get selectedOptions() { return this.options.filter(option => option.selected); }
  set innerHTML(html) {
    this._html = html; this.controls = [];
    if (this.tagName === 'SELECT') {
      this.options = [...html.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)].map(match => {
        const attrs = attributes(match[1]), option = new Element('', 'option');
        option.value = attrs.value ?? decode(match[2]); option.textContent = decode(match[2]);
        option.selected = /\bselected\b/.test(match[1]);
        if ('data-unavailable' in attrs) option.dataset.unavailable = attrs['data-unavailable'];
        return option;
      });
      if (!this.multiple && !this.options.some(option => option.selected) && this.options.length) this.options[0].selected = true;
    }
    for (const match of html.matchAll(/<input\b([^>]*)>|<(select|textarea)\b([^>]*)>([\s\S]*?)<\/(?:select|textarea)>/g)) {
      const attrs = attributes(match[1] || match[3]), control = new Element('', match[2] || 'input');
      for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) {
        control.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
      }
      control.value = attrs.value ?? decode(match[4] || '');
      control.placeholder = attrs.placeholder || '';
      control.checked = /\bchecked\b/.test(match[1] || match[3]);
      if (match[2] === 'select') control.innerHTML = match[4];
      control.parentElement = this; this.controls.push(control);
    }
  }
  get innerHTML() { return this._html || ''; }
  querySelectorAll(selector) {
    if (selector.startsWith('option')) return this.options.filter(option => option.dataset.unavailable);
    if (selector === '[data-strategy-param]') return this.controls.filter(control => control.dataset.strategyParam);
    if (selector.startsWith('[data-symbol]')) return this.controls.filter(control => control.dataset.symbol && (!selector.includes(':checked') || control.checked));
    return [];
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, callback) { const list = this.listeners.get(type) || []; list.push(callback); this.listeners.set(type, list); }
  async emit(type, target = this) {
    const event = {type, target, preventDefault() {}};
    await this['on' + type]?.(event);
    for (const callback of this.listeners.get(type) || []) await callback(event);
    if (this.parentElement?.emit) await this.parentElement.emit(type, target);
  }
  dispatchEvent(event) { this.emit(event.type); }
  setCustomValidity(message) { this.validationMessage = message; }
  replaceChildren(...children) { this.options = children; this.children = children; }
  add(option) { this.options.push(option); }
  appendChild(child) { this.children.push(child); }
  append(child) { this.appendChild(child); }
  cloneNode() {
    const copy = new Element(this.id, this.tagName.toLowerCase()); copy.multiple=this.multiple;
    copy.options=this.options.map(option=>{const cloned=option.cloneNode();cloned.selected=option.selected;return cloned;});
    copy.value = this.value; return copy;
  }
  remove() {} focus() {} scrollIntoView() {} select() {} close() {} showModal() {} before() {}
  getBoundingClientRect() { return {width:0, height:0}; }
}

function page(kind, disk = storage()) {
  const elements = new Map(), form = new Element(kind === 'local' ? 'run-form' : 'research-form', 'form');
  const get = id => {
    if (!elements.has(id)) {
      const select = ['strategy','symbols','benchmark','kind','interval','end-mode','mode','preset-list','custom-files'].includes(id);
      const element = new Element(id, select ? 'select' : 'input'); element.parentElement = new Element('', 'div');
      elements.set(id, element);
    }
    return elements.get(id);
  };
  elements.set(form.id, form);
  const document = {getElementById:get, activeElement:null, body:new Element('', 'body'),
    querySelector: selector => selector.startsWith('#') && !selector.includes(' ') ? get(selector.slice(1)) : null,
    querySelectorAll: () => [], createElement: tag => new Element('', tag)};
  const defaults = {strategy:'dca', kind:'single', start:'2024-01-02', end:'2024-01-05', cash:'1000', leverage:'1',
    mode:'close_research', benchmark:'', commission:'0', slippage:'0', interest:'0', warmup:'0',
    params:'{}', flows:'{}', advanced:'{}', interval:'month', horizon:'3', 'end-mode':'fixed_length', gap:'0'};
  for (const [id,value] of Object.entries(defaults)) {
    const element = get(id);
    if (element.tagName === 'SELECT') element.innerHTML = `<option value="${value}">${value}</option>`;
    element.value = value; element.parentElement = form;
  }
  get('strategy').innerHTML = catalog.strategies.map(item => `<option value="${item.id}">${item.name}</option>`).join('');
  get('strategy').value = 'dca';
  get('symbols').multiple = true;
  get('symbols').innerHTML = '<option value="A" selected>A</option><option value="B" selected>B</option>';
  get('symbols').parentElement = form;
  const context = vm.createContext({document, localStorage:disk, ResearchConfig:Config,
    InvestmentLabStrategyForms:Forms, InvestmentLabUI:UI, TextEncoder, Blob, URL, crypto:require('node:crypto').webcrypto,
    Option:function(text,value) { const option=new Element('', 'option'); option.textContent=text;option.value=value;return option; },
    Event:class {constructor(type){this.type=type;}}, MutationObserver:class {observe(){}}, ResizeObserver:class {observe(){}},
    // Automatic startup stays pending at mocked IO boundaries. No requests,
    // IndexedDB or worker processes can escape this isolated test.
    fetch:() => new Promise(() => {}), indexedDB:{open:() => ({})},
    Worker:class {constructor(){throw new Error('unexpected worker');}},
    setTimeout:() => 0, clearTimeout(){}, setInterval:() => 0, clearInterval(){}, requestAnimationFrame(){}, console});
  context.window = context; context.addEventListener = () => {};
  const source = fs.readFileSync(path.join(root, kind === 'local' ? 'src/investment_lab/web/static/app.js' : 'pages/app.js'), 'utf8');
  // The browser module only needs import.meta inside the blocked worker path.
  vm.runInContext(source.replace(/import\.meta\.url/g, "'https://example.invalid/'"), context);
  context.catalog = catalog.strategies;
  const security = symbol => ({name:symbol,kind:'stock',market:'US',currency:'USD',timezone:'America/New_York'});
  context.records = [{id:'s',name:'sample',first:'2024-01-02',last:'2024-01-05',synthetic:true,
    symbols:{A:security('A'),B:security('B')},catalog:{securities:{A:security('A'),B:security('B')},sessions:['2024-01-02','2024-01-03','2024-01-04','2024-01-05']}}];
  vm.runInContext(`strategyCatalog=catalog;${kind==='local'?'datasets=records;selectedSnapshotIds=["s"];supportsMultiSnapshot=true;':'packages=records;selectedIds=["s"];selectedSymbols=["A","B"];'}renderStrategyParameters(false);`, context);
  return {get, disk, context, run:code=>vm.runInContext(code, context)};
}

function legacyConfig() {
  return {format:Config.format,version:1,name:'旧草稿',snapshots:['s'],symbols:['A','B'],
    fields:{...Object.fromEntries(Config.fields.map(id=>[id,''])),strategy:'dca',kind:'single',cash:'1000',
      start:'2024-01-02',end:'2024-01-05',mode:'close_research',leverage:'1',warmup:'0',
      params:'{\n  "amount": ',flows:'{\n  "monthly": ',advanced:'{ "allow_unverified_actions": '}};
}

test('local page restores a legacy preset without parsing or rewriting unfinished JSON', () => {
  const local = page('local'), saved=legacyConfig(); local.context.saved=saved;
  const warning=local.run('applyConfig(saved)');
  for (const id of ['params','flows','advanced']) assert.equal(local.get(id).value,saved.fields[id]);
  assert.match(warning,/JSON/);assert.equal(local.get('run-button').disabled,true);
  local.run('configReady=true;saveConfigDraft()');
  const refresh=page('local',local.disk);refresh.run('initializeConfigPersistence()');
  for (const id of ['params','flows','advanced']) assert.equal(refresh.get(id).value,saved.fields[id]);
  assert.match(refresh.get('draft-status').textContent,/已恢复/);
});

for (const kind of ['local','pages']) {
  test(`${kind} real request assembly preserves exact automatic equal weights and partial allocations`, async () => {
    const ui=page(kind);ui.get('strategy').value='target_allocation';await ui.get('strategy').emit('change');
    if(kind==='local') {
      ui.get('symbols').innerHTML='<option value="A" selected>A</option><option value="B" selected>B</option><option value="C" selected>C</option>';
    } else ui.run('selectedSymbols=["A","B","C"];runtimeManifest={files:{}}');
    for(const weights of [{},{A:.6}]) {
      const text=' '+JSON.stringify({weights})+' ';
      ui.get('params').value=text;await ui.get('params').emit('input');
      const controls=ui.get('strategy-parameter-fields').controls.filter(input=>input.dataset.strategyParam==='weights');
      assert.equal(controls.length,3);
      assert.equal(controls.find(input=>input.dataset.symbol==='B').value,'');
      assert.match(controls[1].placeholder,Object.keys(weights).length?/未配置/:/自动等权/);
      const params=ui.run(kind==='local'?'currentStrategyParams()':'buildRunRequest().params');
      assert.deepEqual(JSON.parse(JSON.stringify(params.weights)),weights);
      assert.equal(ui.get('params').value,text,'building a request must not format the raw editor');
    }
    const control=ui.get('strategy-parameter-fields').controls.find(input=>input.dataset.symbol==='C');
    control.value='0.2';await control.emit('input');
    assert.deepEqual(JSON.parse(ui.get('params').value).weights,{A:.6,C:.2});
  });

  test(`${kind} page input, strategy switching and reload retain exact malformed drafts`, async () => {
    const ui=page(kind);if(kind==='local')ui.run('configReady=true');
    const text='{\n "amount": ',flows='{ "monthly":';
    ui.get('params').value=text;await ui.get('params').emit('input');
    ui.get('flows').value=flows;await ui.get('flows').emit('input');
    ui.get('strategy').value='buy_hold';await ui.get('strategy').emit('change');
    ui.get('params').value='{"weight":';await ui.get('params').emit('input');
    ui.get('strategy').value='dca';await ui.get('strategy').emit('change');
    assert.equal(ui.get('params').value,text);assert.equal(ui.get('flows').value,flows);
    assert.match(ui.get('strategy-parameter-status').textContent,/JSON/);
    const refreshed=page(kind,ui.disk);
    refreshed.run(kind==='local'?'initializeConfigPersistence()':'restoreDraft();updateSymbols()');
    assert.equal(refreshed.get('params').value,text);assert.equal(refreshed.get('flows').value,flows);
    assert.equal(refreshed.get(kind==='local'?'run-button':'run').disabled,true);
    refreshed.get('strategy').value='buy_hold';await refreshed.get('strategy').emit('change');
    assert.equal(refreshed.get('params').value,'{"weight":','inactive malformed strategy drafts also survive reload');
  });

  test(`${kind} selection changes preserve unselected weights and display validation`, async () => {
    const ui=page(kind);if(kind==='local')ui.run('configReady=true');
    ui.get('strategy').value='target_allocation';await ui.get('strategy').emit('change');
    const text=' { "weights": {"A": 0.6, "B": 0.4}, "frequency": "monthly" } ';
    ui.get('params').value=text;await ui.get('params').emit('input');
    if(kind==='local') {
      ui.get('symbols').options.find(option=>option.value==='B').selected=false;
      await ui.get('symbols').emit('change');
    } else {
      ui.run('updateSymbols()');
      const checkbox=ui.get('symbol-list').controls.find(input=>input.dataset.symbol==='B');
      checkbox.checked=false;await checkbox.emit('change');
    }
    assert.equal(ui.get('params').value,text);
    assert.match(ui.get('strategy-parameter-status').textContent,/未被选中/);
    assert.equal(ui.get(kind==='local'?'run-button':'run').disabled,true);
    await ui.get('strategy-parameter-fields').emit('input');
    assert.equal(ui.get('params').value,text,'a form edit must not erase the hidden B weight');
  });
}

test('local named configuration load keeps exact formatting and invalid per-strategy drafts', async () => {
  const ui=page('local');ui.run('configReady=true');
  ui.get('preset-name').value='未完成配置';ui.get('params').value=' { "amount":';
  await ui.get('params').emit('input');await ui.get('save-preset').emit('click');
  ui.get('params').value='{}';ui.get('preset-list').value='未完成配置';
  await ui.get('load-preset').emit('click');
  assert.equal(ui.get('params').value,' { "amount":');assert.match(ui.get('notice').textContent,/JSON/);
});

test('Pages old object caches and drafts without caches retain the visible raw field', () => {
  for(const strategyParams of [undefined,{dca:{amount:1000},buy_hold:{weight:.5}}]) {
    const disk=storage();disk.setItem('investment-lab-pages-draft-v1',JSON.stringify({fields:{strategy:'dca',params:' { "amount": '},selectedIds:['s'],selectedSymbols:['A','B'],strategyParams}));
    const ui=page('pages',disk);ui.run('restoreDraft();updateSymbols()');
    assert.equal(ui.get('params').value,' { "amount": ');
    assert.match(ui.get('strategy-parameter-status').textContent,/JSON/);
  }
});

const diagnosticResult = {kind:'single',metrics:{total_return:0,final_equity:1000,trades:0},curve:[],trades:[],
  metadata:{currency:'USD',notes:['历史不足，等待信号'],strategy_diagnostics:[{date:'2024-01-02',strategy:'rsi',symbol:'<A>',action:'skip_signal',message:'缺少 <script>alert(1)</script>'}],strategy_diagnostics_omitted:3}};

test('Pages actual result renderer displays escaped diagnostics and supports old results', () => {
  const ui=page('pages');ui.context.result=diagnosticResult;ui.run('renderResult(result)');
  const html=ui.get('result-detail').innerHTML;
  assert.match(html,/策略与数据提示/);assert.match(html,/策略诊断明细/);assert.match(html,/另有 3 项未保存/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
  assert.match(html,/等待完整指标后发出信号/);
  ui.context.result={...diagnosticResult,metadata:{}};ui.run('renderResult(result)');
  assert.doesNotMatch(ui.get('result-detail').innerHTML,/策略诊断明细/);
});

test('local actual run detail shows escaped strategy diagnostics', async () => {
  const ui=page('local');ui.context.detail={result:diagnosticResult,request:{synthetic:true,snapshot:'s',config:{symbols:['A'],mode:'close_research'}}};
  ui.run("api=async path=>path.includes('/table?')?{items:[],total:0}:detail");
  await ui.run("openRun('test-run',0,'holdout',false)");
  const html=ui.get('result-detail').innerHTML;
  assert.match(html,/策略与数据提示/);assert.match(html,/策略诊断明细/);assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
});
