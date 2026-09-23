const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = (x) => x == null ? '—' : (x * 100).toFixed(2) + '%';
const num = (x) => x == null ? '—' : Number(x).toLocaleString('zh-CN',{maximumFractionDigits:2});
let datasets = [], instruments = [], currentRun = null, currentSample = 0, currentSection = 'holdout', tableOffset = 0, tableName = 'trades';
let polling = null, pollingRun = null, detailRequest = 0, pollInFlight = false;
let selectedSnapshotIds = [];
let supportsMultiSnapshot = false;
const modeNames={strict_vwap:'严格 VWAP',estimated_vwap:'估算 VWAP',close_research:'收盘价研究',reference_research:'参考历史价格研究'};
const isReference=request=>request.config.mode==='reference_research';
const runDataLabel=request=>request.synthetic?'合成演示':isReference(request)?'参考研究':'真实来源';
const referenceNote='使用拆股调整后的参考收盘价，不代表实际成交价；忽略拆股和现金分红事件，收益不含股息，数量为参考份额，不进入正常排名。';
function notice(text, error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; $('notice').hidden=false; }
async function api(path, body) { const r=await fetch('/api'+path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Request':'local-ui'},body:JSON.stringify(body)}); const data=await r.json(); if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)); return data; }
function safe(fn){return async (...args)=>{try{await fn(...args);}catch(e){notice(e.message,true);}};}
function view(name) { document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-'+name); document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===name)); $('page-title').textContent=document.querySelector(`nav button[data-view="${name}"]`).textContent; if(name==='runs')safe(loadRuns)(); if(name==='data')safe(loadData)(); if(name==='universe')safe(loadUniverse)(); if(name==='backups')safe(loadBackups)(); }
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>view(b.dataset.view));
async function loadStatus(){const s=await api('/status');supportsMultiSnapshot=!!s.features?.includes('multi_snapshot');$('version').textContent='v'+s.version+' · 本机研发版';$('system-state').textContent=`${s.datasets} 个数据集 · ${s.real_datasets} 个真实来源`;$('paths').textContent=`代码：${s.project_path}\n数据：${s.data_path}\nSchema：${s.schema}`;$('run-button').disabled=!!datasetSelectionError();updatePriceModeNote();}
const selectedDatasets=()=>selectedSnapshotIds.map(id=>datasets.find(d=>d.id===id)).filter(Boolean);
function datasetSelectionError(selected=selectedDatasets(),checkForm=true){
 if(selectedSnapshotIds.some(id=>!datasets.some(d=>d.id===id)))return '已保存的部分快照不在当前列表中；原编号仍保留，请检查数据或手动取消后重选。不会自动替换为新版快照。';
 if(checkForm&&document.querySelector('#run-form option[data-unavailable]:checked'))return '已保存的策略、标的或基准当前不可用，请检查并重新选择。';
 if(!selected.length)return '请至少勾选一个数据快照。';
 if(selected.some(d=>!d.first||!d.last))return '所选快照缺少交易日历，请先检查数据覆盖。';
 if(selected.length===1)return supportsMultiSnapshot?'':'后台仍是旧版，请重启投资研究室服务后运行。';
 if(new Set(selected.map(d=>d.synthetic)).size>1)return '合成演示与真实来源不能混用，请取消其中一类。';
 const scopes=new Set(),seen=new Set(),identities=new Set();
 for(const d of selected)for(const [symbol,s] of Object.entries(d.symbols)){
  const identity=[s.market,s.universe_id||symbol].join('/');
  if(seen.has(symbol)||identities.has(identity))return `重复标的 ${s.universe_id||symbol}：每个标的请只勾选一份行情来源。`;
  seen.add(symbol);identities.add(identity);scopes.add([s.market,s.currency,s.timezone].join('/'));
 }
 if(scopes.size>1)return '同一次回测只允许同一市场、币种和时区，请调整勾选。';
 const providers=new Set(selected.map(d=>d.source?.provider));
 if(providers.has('Yahoo/chart')&&providers.size>1)return 'Yahoo参考价格与其他价格来源不能混合，请调整勾选。';
 if(selected.map(d=>d.first).sort().at(-1)>=selected.map(d=>d.last).sort()[0])return '所选快照没有足够的共同覆盖区间。';
 if(!supportsMultiSnapshot)return '后台仍是旧版，请重启投资研究室服务后运行。';
 return '';
}
async function loadDatasets(){
 datasets=await api('/datasets');datasets.sort((a,b)=>Number(b.synthetic)-Number(a.synthetic));
 $('dataset-options').innerHTML=datasets.map(d=>`<label class="dataset-option"><input type="checkbox" name="snapshots" value="${esc(d.id)}" ${selectedSnapshotIds.includes(d.id)?'checked':''}><span><strong>${esc(d.name)}</strong><small>${esc(d.first)} → ${esc(d.last)}</small></span></label>`).join('');
 setDatasets();filterSnapshotOptions();
}
function filterSnapshotOptions(){
 const query=$('dataset-search').value.trim().toLowerCase();let visible=0;
 document.querySelectorAll('#dataset-options .dataset-option').forEach(row=>{
  const d=datasets.find(d=>d.id===row.querySelector('input').value);
  const text=[d.name,...Object.entries(d.symbols).flatMap(([symbol,s])=>[symbol,s.name,s.market,s.currency])].join(' ').toLowerCase();
  row.hidden=!!query&&!text.includes(query);if(!row.hidden)visible++;
 });
 $('dataset-empty').hidden=visible>0;
 $('dataset-match-count').textContent=`显示 ${visible} / ${datasets.length}`;
}
function setDatasets(){
 const selected=selectedDatasets(),securities=Object.assign({},...selected.map(d=>d.symbols));
 const previousSymbols=new Set([...$('symbols').selectedOptions].map(o=>o.value));
 const knownSymbols=new Set([...$('symbols').options].map(o=>o.value));
 const previousBenchmark=$('benchmark').value;
 $('symbols').innerHTML=Object.entries(securities).map(([s,x])=>`<option value="${esc(s)}" ${previousSymbols.has(s)||(!knownSymbols.has(s)&&x.kind!=='index')?'selected':''}>${esc(x.name)} · ${esc(s)}</option>`).join('');
 $('symbols').disabled=!selected.length;
 $('benchmark').innerHTML='<option value="">不设置</option>'+Object.entries(securities).filter(([s,x])=>x.kind!=='index').map(([s,x])=>`<option value="${esc(s)}">${esc(x.name)}</option>`).join('');
 if(securities[previousBenchmark])$('benchmark').value=previousBenchmark;
 $('dataset-count').textContent=`已选 ${selectedSnapshotIds.length} 个快照 · ${Object.keys(securities).length} 个标的`;
 $('dataset-selected').innerHTML=selectedSnapshotIds.map(id=>{const d=datasets.find(d=>d.id===id),name=d?.name||`当前列表缺少快照 ${id.slice(0,12)}`;return `<button type="button" data-remove-snapshot="${esc(id)}" aria-label="取消选择 ${esc(name)}">${esc(name)} <span aria-hidden="true">×</span></button>`;}).join('');
 $('dataset-clear').disabled=!selectedSnapshotIds.length;
 const error=datasetSelectionError(selected);$('run-button').disabled=!!error;
 if(!datasetSelectionError(selected,false)){
  const first=selected.map(d=>d.first).sort().at(-1),last=selected.map(d=>d.last).sort()[0];
  for(const id of ['start','end']){$(id).min=first;$(id).max=last;}
  if(!$('start').value||$('start').value<first||$('start').value>last)$('start').value=first;
  if(!$('end').value||$('end').value>last||$('end').value<first)$('end').value=last;
  if($('start').value>=$('end').value){$('start').value=first;$('end').value=last;}
  $('dataset-range').textContent=`共同覆盖：${first} → ${last}。缺失行情仍会报告。`;
  if(selected.every(d=>d.source?.provider==='Yahoo/chart')&&Object.values(securities).some(s=>s.kind!=='index'))$('mode').value='reference_research';
  else if($('mode').value==='reference_research')$('mode').value='strict_vwap';
 }else $('dataset-range').textContent='';
 updatePriceModeNote();updateStrategyScopeNote();
}
$('dataset-options').onchange=e=>{
 const box=e.target;if(!box.matches('input[name="snapshots"]'))return;
 if(box.checked)selectedSnapshotIds.push(box.value);else selectedSnapshotIds=selectedSnapshotIds.filter(id=>id!==box.value);
 setDatasets();
};
$('dataset-search').oninput=filterSnapshotOptions;
$('dataset-search').onkeydown=e=>{if(e.key==='Enter')e.preventDefault();};
$('dataset-clear').onclick=()=>{selectedSnapshotIds=[];document.querySelectorAll('#dataset-options input').forEach(b=>b.checked=false);setDatasets();};
$('dataset-selected').onclick=e=>{
 const button=e.target.closest('[data-remove-snapshot]');if(!button)return;
 const id=button.dataset.removeSnapshot;selectedSnapshotIds=selectedSnapshotIds.filter(s=>s!==id);
 document.querySelectorAll('#dataset-options input').forEach(b=>{if(b.value===id)b.checked=false;});setDatasets();
};
function updateStrategyScopeNote(){
 const symbols=[...$('symbols').selectedOptions],strategy=$('strategy').value;
 const single=['buy_hold','dca','moving_average','drawdown_buy','leverage_rebalance'].includes(strategy);
 $('strategy-scope-note').textContent=symbols.length>1&&single?`此内置策略只交易第一项：${symbols[0].textContent}。多标的交易可选“同市场轮动”或使用 Python 策略；勾选数据不会自动分配仓位。`:'所选标的共用同一账户和初始资金，实际买卖由策略决定。';
}
$('symbols').onchange=()=>{updateStrategyScopeNote();refreshConfigValidity();};
$('strategy').onchange=()=>{updateStrategyScopeNote();refreshConfigValidity();};
$('benchmark').onchange=refreshConfigValidity;
function dataSourcesHtml(request){
 const sources=request.data_sources||[{snapshot:request.snapshot,name:'原始数据快照'}];
 return `<details class="data-sources"><summary>本次使用 ${sources.length} 个数据快照 · 同一账户</summary><ul>${sources.map(s=>`<li>${esc(s.name)}<small>${esc(s.snapshot)}</small></li>`).join('')}</ul><p class="help">交易标的：${esc(request.config.symbols.join('、'))}</p></details>`;
}
function updatePriceModeNote(){
 const selected=selectedDatasets(),d=selected[0],error=datasetSelectionError(selected);
 if(error){$('dataset-note').textContent=error;$('dataset-note').className='callout'+(selected.length?' invalid':'');$('price-mode-note').textContent='';return;}
 const reference=$('mode').value==='reference_research';
 const available=selected.some(d=>d.reference_research_available)&&selected.every(d=>d.reference_research_available||(d.source?.provider==='Yahoo/chart'&&Object.values(d.symbols).every(s=>s.kind==='index')));
 $('dataset-note').textContent=d.synthetic?'合成演示 · 非真实行情，仅用于验证平台流程。':reference?(available?'已选择参考历史价格研究。'+referenceNote:'所选数据不支持参考历史价格模式，请选择其他价格模式。'):available?'所选数据没有已核实的实际成交价。请选择“参考历史价格研究”运行；严格模式仍会阻止。':'真实来源 · 先查看覆盖与 VWAP 口径；未验证字段会阻止严格回测。';
 $('dataset-note').className='callout'+(d.synthetic||reference?' demo':'');
 $('price-mode-note').textContent=reference?referenceNote:'下一交易日按所选价格模型模拟成交。';
 $('execution-price-label').textContent=reference?'参考收盘价 + 滑点':$('mode').value==='close_research'?'收盘价 + 滑点':'VWAP + 滑点';
}
$('mode').onchange=updatePriceModeNote;
$('monthly-example').onclick=safe(()=>{
 const flows=JSON.parse($('flows').value||'{}');
 if(!flows||typeof flows!=='object'||Array.isArray(flows))throw new Error('入金/提款须为 JSON 对象');
 flows.monthly=3000;
 $('flows').value=JSON.stringify(flows,null,2);$('flows').focus();
 notice('已填入每月入金3000，原有日期金额保留；入金与策略买入金额分别设置。');
});
function cashFlowPlanHtml(metadata){
 const plan=metadata?.cash_flows;
 if(!plan||!Object.keys(plan).length)return '';
 return `<details><summary>本区间入金 / 提款计划（${Object.keys(plan).length} 个日期）</summary><p class="help">${esc(metadata.monthly_deposit_rule||'按指定交易日处理；金额为同日合计。')}</p><pre>${esc(JSON.stringify(plan,null,2))}</pre></details>`;
}
let configReady=false, restoringConfig=false;
function configRepository(){return ResearchConfig.repository(window.localStorage);}
function currentConfig(){return ResearchConfig.capture(document,selectedSnapshotIds);}
function refreshConfigValidity(){
 $('run-button').disabled=!!datasetSelectionError();updatePriceModeNote();
}
function saveConfigDraft(){
 if(!configReady||restoringConfig)return;
 try{
  configRepository().saveDraft(currentConfig());
  $('draft-status').textContent='草稿已自动保存 · '+new Date().toLocaleTimeString('zh-CN');
  $('draft-status').classList.remove('run-error');
 }catch(e){
  $('draft-status').textContent='草稿未保存，请导出 JSON 备份。'+e.message;
  $('draft-status').classList.add('run-error');
 }
}
function refreshPresetList(selected=$('preset-list').value){
 const names=configRepository().list();
 $('preset-list').replaceChildren(new Option(names.length?'选择一份已保存的配置':'暂无已保存配置',''));
 for(const name of names)$('preset-list').add(new Option(name,name));
 if(names.includes(selected))$('preset-list').value=selected;
 $('load-preset').disabled=!$('preset-list').value;
}
function restoreSelect(id,values){
 const select=$(id);
 select.querySelectorAll('option[data-unavailable]').forEach(o=>o.remove());
 for(const value of values){
  if(![...select.options].some(o=>o.value===value)){
   const option=new Option('当前不可用 · '+value,value);option.dataset.unavailable='true';select.add(option);
  }
 }
 for(const option of select.options)option.selected=values.includes(option.value);
 if(select.multiple){
  const options=[...select.options];
  select.replaceChildren(...values.map(value=>options.find(o=>o.value===value)),...options.filter(o=>!values.includes(o.value)));
 }
}
function applyConfig(input){
 const config=ResearchConfig.validate(input);
 // Validate representability before changing any form values (e.g. malformed dates).
 for(const id of ResearchConfig.fields){
  if(['strategy','benchmark'].includes(id))continue;
  const copy=$(id).cloneNode(true);copy.value=config.fields[id];
  if(copy.value!==config.fields[id])throw new Error(`配置字段 ${id} 无法载入，当前输入未替换。`);
 }
 restoringConfig=true;
 try{
  selectedSnapshotIds=[...config.snapshots];
  document.querySelectorAll('#dataset-options input').forEach(box=>box.checked=selectedSnapshotIds.includes(box.value));
  setDatasets();
  // Restore after dataset defaults, so dates, price mode and symbol choices stay exact.
  for(const id of ResearchConfig.fields){
   if(['strategy','benchmark'].includes(id))restoreSelect(id,[config.fields[id]]);
   else $(id).value=config.fields[id];
  }
  restoreSelect('symbols',config.symbols);
  $('preset-name').value=config.name;
  $('dataset-search').value='';filterSnapshotOptions();
  $('kind').onchange();updateStrategyScopeNote();refreshConfigValidity();
 }finally{restoringConfig=false;}
 return datasetSelectionError();
}
function initializeConfigPersistence(){
 try{
  refreshPresetList();
  const draft=configRepository().draft();
  if(draft){
   const warning=applyConfig(draft);
   $('draft-status').textContent='已恢复上次草稿。';
   if(warning)notice('已恢复草稿；'+warning,true);
  }else $('draft-status').textContent='修改后自动保存草稿，刷新或重试可继续填写。';
 }catch(e){
  $('draft-status').textContent='未能恢复本地配置；可以继续填写或导入 JSON。'+e.message;
  $('draft-status').classList.add('run-error');
 }finally{configReady=true;$('config-tools').disabled=false;}
}
$('run-form').addEventListener('input',e=>{
 if(ResearchConfig.fields.includes(e.target.id)||e.target.id==='preset-name')saveConfigDraft();
});
$('run-form').addEventListener('change',e=>{
 if(ResearchConfig.fields.includes(e.target.id)||e.target.id==='symbols'||e.target.name==='snapshots')saveConfigDraft();
});
$('dataset-clear').addEventListener('click',saveConfigDraft);
$('dataset-selected').addEventListener('click',e=>{if(e.target.closest('[data-remove-snapshot]'))saveConfigDraft();});
$('monthly-example').addEventListener('click',saveConfigDraft);
$('run-form').addEventListener('submit',saveConfigDraft,true);
$('preset-name').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('save-preset').click();}};
$('preset-list').onchange=()=>{$('load-preset').disabled=!$('preset-list').value;};
$('save-preset').onclick=safe(()=>{
 const name=$('preset-name').value.trim(),repo=configRepository();
 const exists=repo.list().includes(name);
 if(exists&&!window.confirm(`覆盖已保存的配置“${name}”？`))return;
 const saved=repo.save(name,currentConfig(),exists);
 $('preset-name').value=saved;refreshPresetList(saved);saveConfigDraft();notice(`配置“${saved}”已保存。`);
});
$('load-preset').onclick=safe(()=>{
 const name=$('preset-list').value;if(!name)return;
 const warning=applyConfig(configRepository().load(name));saveConfigDraft();
 notice(`已载入配置“${name}”。`+(warning?' '+warning:' 可以修改后运行。'),!!warning);
});
$('export-config').onclick=safe(()=>{
 $('config-json').value=ResearchConfig.encode(currentConfig());
 $('config-json').readOnly=true;$('apply-config-json').hidden=true;$('download-config-json').hidden=false;$('choose-config-file').hidden=true;
 $('config-dialog-title').textContent='导出配置 JSON';$('config-dialog-status').textContent='可下载文件，也可复制以下全文保存为 .json 文件。';
 $('config-dialog').showModal();$('config-json').select();
});
$('download-config-json').onclick=safe(()=>{
 const config=ResearchConfig.decode($('config-json').value),blob=new Blob([ResearchConfig.encode(config)],{type:'application/json'});
 const url=URL.createObjectURL(blob),link=document.createElement('a');
 link.href=url;link.download=(config.name.trim()||'投资研究室配置').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_')+'.json';
 document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
 $('config-dialog-status').textContent='已请求下载；若应用窗口没有出现下载，请复制下方全文保存为 .json 文件。';
});
function importConfigText(text){
 const warning=applyConfig(ResearchConfig.decode(text));
 $('preset-list').value='';$('load-preset').disabled=true;saveConfigDraft();$('config-dialog').close();
 notice('已导入配置；可填写名称后保存。'+(warning?' '+warning:''),!!warning);
}
$('import-config').onclick=()=>{
 $('config-json').value='';$('config-json').readOnly=false;$('apply-config-json').hidden=false;$('download-config-json').hidden=true;$('choose-config-file').hidden=false;
 $('config-dialog-title').textContent='导入配置 JSON';$('config-dialog-status').textContent='选择 JSON 文件或粘贴完整配置，然后载入。';$('config-dialog').showModal();
};
$('close-config-dialog').onclick=()=>$('config-dialog').close();
$('apply-config-json').onclick=()=>{
 try{importConfigText($('config-json').value);}catch(e){$('config-dialog-status').textContent=e.message;}
};
$('choose-config-file').onclick=()=>$('config-file').click();
$('config-file').onchange=safe(async e=>{
 const file=e.target.files[0];if(!file)return;
 try{
  if(file.size>ResearchConfig.maxBytes)throw new Error('配置文件不能超过 1 MB。');
  const text=await file.text();ResearchConfig.decode(text);$('config-json').value=text;
  $('config-dialog-status').textContent='文件已读取，点击“载入 JSON”应用到表单。';
 }catch(e){$('config-dialog-status').textContent=e.message;
 }finally{e.target.value='';}
});
window.addEventListener('storage',e=>{
 if(e.key?.startsWith('investment-lab.research-preset.v1:'))safe(()=>refreshPresetList())();
});
async function loadStrategies(){const s=await api('/strategies');const old=$('strategy').value;$('strategy').innerHTML=Object.entries(s.builtins).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')+s.custom.map(n=>`<option value="custom:${esc(n)}">Python · ${esc(n)}</option>`).join('');if(old)restoreSelect('strategy',[old]);$('custom-files').innerHTML='<option value="">新建策略</option>'+s.custom.map(n=>`<option>${esc(n)}</option>`).join('');if(configReady)refreshConfigValidity();}
$('kind').onchange=()=>{$('rolling-options').hidden=$('kind').value!=='rolling';$('holdout-options').hidden=$('kind').value!=='holdout';};
$('run-form').onsubmit=safe(async e=>{e.preventDefault();const selectionError=datasetSelectionError();if(selectionError)throw new Error(selectionError);const config={start:$('start').value,end:$('end').value,symbols:[...$('symbols').selectedOptions].map(o=>o.value),initial_cash:$('cash').value,max_leverage:$('leverage').value,mode:$('mode').value,commission_bps:$('commission').value,slippage_bps:$('slippage').value,annual_interest:$('interest').value,warmup:Number($('warmup').value),cash_flows:JSON.parse($('flows').value),benchmark:$('benchmark').value||null,...JSON.parse($('advanced').value)};const chosen=$('strategy').value;const request={...(selectedSnapshotIds.length===1?{snapshot:selectedSnapshotIds[0]}:{snapshots:[...selectedSnapshotIds]}),config,strategy:chosen.startsWith('custom:')?'custom':chosen,params:JSON.parse($('params').value),kind:$('kind').value};if(chosen.startsWith('custom:'))request.strategy_file=chosen.slice(7);if(request.kind==='rolling')request.research={interval:$('interval').value,horizon:Number($('horizon').value),end_mode:$('end-mode').value};if(request.kind==='holdout')request.research={test_start:$('test-start').value,gap_sessions:Number($('gap').value)};$('run-button').disabled=true;try{const r=await api('/runs',request);currentRun=r.run_id;view('runs');await openRun(r.run_id,0,'holdout',false);}finally{$('run-button').disabled=!!datasetSelectionError();}});
const statusNames={queued:'排队中',running:'计算中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
function progressInfo(d){const p=d.progress,done=Number(p?.done),total=Number(p?.total),valid=Number.isFinite(done)&&Number.isFinite(total)&&total>0,completed=valid?Math.min(total,Math.max(0,done)):0,percent=valid?Math.floor(completed/total*100):0,eta=p?.eta_seconds;return{valid,done:completed,total,percent,eta:eta===null||eta===undefined?null:Number(eta)};}
function durationText(seconds){if(seconds===null||!Number.isFinite(seconds))return '估算中';if(seconds<=0)return '即将完成';const s=Math.ceil(seconds);if(s<60)return `${s} 秒`;if(s<3600)return `${Math.floor(s/60)} 分 ${s%60} 秒`;return `${Math.floor(s/3600)} 小时 ${Math.floor(s%3600/60)} 分钟`;}
function progressLabel(d){const p=progressInfo(d);if(!p.valid)return d.status==='queued'?'任务排队中，正在准备计算…':'正在准备计算…';return `计算进度 ${p.done} / ${p.total}（${p.percent}%）`;}
function progressPanelHtml(id,d){const p=progressInfo(d),label=progressLabel(d),now=p.valid?`aria-valuenow="${p.percent}" aria-valuetext="${esc(label)}"`:'';return `<div class="run-progress" id="run-progress-panel" data-run-id="${esc(id)}"><p class="run-progress-label" id="run-progress-label">${esc(label)}</p><div class="run-progress-track${p.valid?'':' indeterminate'}" role="progressbar" aria-label="回测计算进度" aria-valuemin="0" aria-valuemax="100" ${now}><span class="run-progress-fill" id="run-progress-fill" style="width:${p.percent}%"></span></div><p class="help run-progress-eta" id="run-progress-eta">预计剩余时间：${esc(durationText(p.eta))}</p></div>`;}
function updateProgressDisplay(id,d){const label=progressLabel(d),eta=durationText(progressInfo(d).eta);notice(`${label} · 预计剩余时间：${eta}。可以继续浏览其他页面。`);const panel=$('run-progress-panel');if(!panel||panel.dataset.runId!==id)return;const p=progressInfo(d),track=panel.querySelector('[role="progressbar"]');$('run-progress-label').textContent=label;$('run-progress-eta').textContent=`预计剩余时间：${eta}`;$('run-progress-fill').style.width=`${p.percent}%`;track.classList.toggle('indeterminate',!p.valid);if(p.valid){track.setAttribute('aria-valuenow',String(p.percent));track.setAttribute('aria-valuetext',label);}else{track.removeAttribute('aria-valuenow');track.removeAttribute('aria-valuetext');}}
async function loadRuns(){const rs=await api('/runs');$('runs-body').innerHTML=rs.length?rs.map(r=>`<tr><td><b>${esc(r.id.slice(0,10))}</b><small>${esc(new Date(r.created).toLocaleString('zh-CN'))}</small></td><td>${esc(r.config.strategy)}<small>${esc({single:'单次回测',rolling:'滚动起点',holdout:'留出验证'}[r.kind])}</small></td><td><span class="badge ${r.config.synthetic||isReference(r.config)?'demo':''}">${runDataLabel(r.config)}</span></td><td><span class="badge ${r.status==='completed'?'good':r.status==='failed'?'bad':''}">${esc(statusNames[r.status]||r.status)}</span>${r.status==='failed'?`<small class="run-error">${esc(r.summary?.message||'失败原因尚未记录，请查看详情。')}</small>`:''}</td><td><button data-open-run="${r.id}">${r.status==='failed'?'查看原因':'查看'}</button> ${['running','queued'].includes(r.status)?`<button data-cancel="${r.id}">取消</button>`:''}</td></tr>`).join(''):'<tr><td colspan="5">暂无运行。前往策略研究配置第一次回测。</td></tr>';document.querySelectorAll('[data-open-run]').forEach(b=>b.onclick=safe(()=>openRun(b.dataset.openRun)));document.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=safe(async()=>{await api('/runs/'+b.dataset.cancel+'/cancel',{});notice('任务已取消，记录和日志已保留。');await loadRuns();}));return rs;}
$('refresh-runs').onclick=safe(loadRuns);
function stopPolling(id){if(id&&pollingRun!==id)return;if(polling)clearInterval(polling);polling=null;pollingRun=null;}
function poll(id=currentRun){if(!id)return;if(polling)clearInterval(polling);pollingRun=id;pollInFlight=false;const tick=safe(async()=>{if(pollInFlight||!pollingRun)return;const runId=pollingRun;pollInFlight=true;try{const d=await api('/runs/'+runId);if(pollingRun!==runId)return;const status=d.status||(d.error?'failed':'queued');if(['completed','failed','cancelled','interrupted'].includes(status)){stopPolling(runId);await loadRuns();if(currentRun===runId&&!$('view-runs').hidden){$('notice').hidden=true;await openRun(runId,currentSample,currentSection,false);}else notice(`运行 ${runId.slice(0,10)} ${statusNames[status]||status}。`);return;}if(['queued','running'].includes(status))updateProgressDisplay(runId,d);}finally{pollInFlight=false;}});polling=setInterval(tick,1800);tick();}
function metricHtml(m){return `<div class="metrics"><div class="metric ${m.total_return>=0?'positive':'negative'}"><span>时间加权收益</span><strong>${pct(m.total_return)}</strong></div><div class="metric negative"><span>最大回撤</span><strong>${pct(m.max_drawdown)}</strong></div><div class="metric"><span>期末权益</span><strong>${num(m.final_equity)}</strong></div><div class="metric"><span>融资成本 / 费用</span><strong>${num(m.interest)} / ${num(m.fees)}</strong></div></div>`;}
function drawChart(canvas,curve,benchmark){if(!canvas||!curve?.length)return;const rect=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;canvas.width=Math.max(300,rect.width)*scale;canvas.height=rect.height*scale;const g=canvas.getContext('2d');g.scale(scale,scale);const w=canvas.width/scale,h=canvas.height/scale,p={l:55,r:15,t:20,b:35};const a=curve.map(r=>Number(r.twr_index));const b=benchmark?.map(r=>Number(r.twr_index))||[];let min=Math.min(...a,...b),max=Math.max(...a,...b);if(max===min){min-=.02;max+=.02;}const span=max-min;min-=span*.08;max+=span*.08;const x=i=>p.l+i/(a.length-1||1)*(w-p.l-p.r),y=v=>p.t+(max-v)/(max-min)*(h-p.t-p.b);g.font='12px Segoe UI';g.textAlign='right';for(let i=0;i<=4;i++){const val=min+(max-min)*i/4;g.strokeStyle='#e6edf6';g.beginPath();g.moveTo(p.l,y(val));g.lineTo(w-p.r,y(val));g.stroke();g.fillStyle='#7b8ba1';g.fillText(val.toFixed(2),p.l-9,y(val)+4);}g.beginPath();a.forEach((v,i)=>i?g.lineTo(x(i),y(v)):g.moveTo(x(i),y(v)));g.lineTo(x(a.length-1),h-p.b);g.lineTo(x(0),h-p.b);g.closePath();const gradient=g.createLinearGradient(0,p.t,0,h-p.b);gradient.addColorStop(0,'#4979ec30');gradient.addColorStop(1,'#4979ec02');g.fillStyle=gradient;g.fill();g.strokeStyle='#336be7';g.lineWidth=2;g.beginPath();a.forEach((v,i)=>i?g.lineTo(x(i),y(v)):g.moveTo(x(i),y(v)));g.stroke();if(b.length){g.strokeStyle='#e3a64d';g.setLineDash([5,4]);g.beginPath();b.forEach((v,i)=>{const bx=p.l+i/(b.length-1||1)*(w-p.l-p.r);i?g.lineTo(bx,y(v)):g.moveTo(bx,y(v));});g.stroke();g.setLineDash([]);}g.fillStyle='#7b8ba1';g.textAlign='left';g.fillText(curve[0].date,p.l,h-10);g.textAlign='right';g.fillText(curve.at(-1).date,w-p.r,h-10);}
async function retryWithReference(request){
 const button=$('retry-reference');button.disabled=true;
 try{
  const r=await api('/runs',{snapshot:request.snapshot,config:{...request.config,mode:'reference_research'},strategy:request.strategy,params:request.params,kind:request.kind,research:request.research,reference_retry_of:request.run_id});
  currentRun=r.run_id;await loadRuns();await openRun(r.run_id);
 }finally{button.disabled=false;}
}
function revealRunDetail() {
 const target=$('result-detail');
 if($('view-runs').hidden)return;
 target.focus({preventScroll:true});
 target.scrollIntoView({block:'start'});
}
function resultHeader(id) {
 return `<div class="section-bar"><h2>运行 ${esc(id.slice(0,10))}</h2><button id="back-to-runs">返回任务列表 ↑</button></div>`;
}
function bindRunDetail(reveal) {
 $('back-to-runs').onclick=()=>{const list=$('runs-list');list.focus({preventScroll:true});list.scrollIntoView({block:'start'});};
 if(reveal)revealRunDetail();
}
function runFailureAdvice(message,referenceAvailable=false) {
 if(referenceAvailable)return '这份数据可用于“参考历史价格研究”。可按原参数使用参考数据重新运行；结果仅供参考，不含股息，不代表实际成交回测。';
 if(message.includes('仅可查阅'))return '该数据集目前仅供查阅，切换策略或成交价格模式仍不能用于回测。请先补齐并验证实际交易价格、公司行动及所需成交字段；若只是体验平台，可返回策略配置选择“合成演示 · 非真实行情”。';
 if(/VWAP|vwap/.test(message))return '当前所选模式要求的 VWAP 数据不完整或口径未验证。请检查数据覆盖并补齐字段；合成演示可用于验证操作流程。';
 return '请按上面的原因检查配置或数据，再重新提交。失败记录和原始诊断会保留。';
}
async function openRun(id,sample=0,section='holdout',reveal=true){
 const token=++detailRequest;
 currentRun=id;currentSample=sample;currentSection=section;tableOffset=0;
 const target=$('result-detail');
 if(reveal){$('notice').hidden=true;target.innerHTML=resultHeader(id)+'<div class="panel" role="status">正在加载运行详情…</div>';bindRunDetail(true);}
 let d;
 try{d=await api(`/runs/${id}?sample=${sample}&section=${section}`);}
 catch(e){
  if(token!==detailRequest)return;
  target.innerHTML=resultHeader(id)+`<div class="panel"><h3>无法加载运行详情</h3><p class="run-error" role="alert">${esc(e.message)}</p><button id="retry-run">重试加载</button></div>`;
  $('retry-run').onclick=safe(()=>openRun(id,sample,section));bindRunDetail(reveal);return;
 }
 if(token!==detailRequest)return;
 if(!d.result){
  const status=d.status||(d.error?'failed':'queued');
  const terminal=['failed','cancelled','interrupted'].includes(status);
  const active=['queued','running'].includes(status),message=d.error?.message||(terminal?'本次运行没有保存具体原因。请保留运行编号以便排查。':status==='queued'?'任务已排队，正在准备计算。':'回测正在计算，进度与预计剩余时间会自动更新。');
  target.innerHTML=resultHeader(id)+`<div class="panel"><h3>${esc(statusNames[status]||status)}</h3>${active?progressPanelHtml(id,d):`<p class="${terminal?'run-error':'help'}" role="${terminal?'alert':'status'}">${esc(message)}</p>`}<p class="help">标的：${esc(d.request.config.symbols.join('、'))} · ${esc(d.request.config.start)} → ${esc(d.request.config.end)}</p>${status==='failed'?`<p>${esc(runFailureAdvice(message,d.reference_research_available&&!isReference(d.request)))}</p>`:''}<div class="result-tabs">${status==='failed'&&d.reference_research_available&&!isReference(d.request)&&d.request.strategy!=='custom'?'<button class="primary" id="retry-reference">按原参数使用参考数据重跑</button>':''}<button id="run-to-data">前往数据覆盖</button><button id="run-to-research">返回策略配置</button></div>${dataSourcesHtml(d.request)}<details><summary>原始诊断与运行配置</summary><pre>${esc(JSON.stringify({run_id:id,status,snapshot:d.request.snapshot,config:d.request.config,error:d.error,progress:d.progress},null,2))}</pre></details></div>`;
  if($('retry-reference'))$('retry-reference').onclick=safe(()=>retryWithReference(d.request));
  $('run-to-data').onclick=()=>{view('data');$('data-filter').focus();};
  $('run-to-research').onclick=()=>{view('research');$('dataset-search').focus();};
  bindRunDetail(reveal);if(active){updateProgressDisplay(id,d);if(pollingRun!==id)poll(id);}else{stopPolling(id);if(reveal&&pollingRun===null)$('notice').hidden=true;}return;
 }
 stopPolling(id);
 const r=d.result;let experiment='';if(d.experiments?.samples){experiment=`<div class="panel"><div class="panel-heading"><h3>起点热力图</h3><span class="muted">${d.experiments.summary.count} 个有效窗口 · 亏损比例 ${pct(d.experiments.summary.loss_ratio)}</span></div><p class="help">${esc(d.experiments.metadata.overlap_warning)}。中位数 ${pct(d.experiments.summary.median)}，10% / 90% 分位数 ${pct(d.experiments.summary.q10)} / ${pct(d.experiments.summary.q90)}。</p><div class="heatmap">${d.experiments.samples.map((s,i)=>`<button data-sample="${i}" class="${s.status==='completed'?(s.metrics.total_return>=0?'gain':'loss'):''}" title="${esc(s.reason||s.end)}"><small>${esc(s.start)}</small>${s.status==='completed'?pct(s.metrics.total_return):'跳过'}</button>`).join('')}</div></div>`;}else if(d.experiments?.holdout){experiment=`<div class="panel"><div class="panel-heading"><h3>开发区间与留出验证</h3><span class="badge">${esc(d.experiments.metadata.label)}</span></div><div class="fields"><div><p>开发区间收益 ${pct(d.experiments.development.total_return)}</p><p>最大回撤 ${pct(d.experiments.development.max_drawdown)}</p><button data-section="development">查看开发区间</button></div><div><p>留出区间收益 ${pct(d.experiments.holdout.total_return)}</p><p>最大回撤 ${pct(d.experiments.holdout.max_drawdown)}</p><button data-section="holdout">查看留出区间</button></div></div><p class="help">${esc(d.experiments.metadata.initialization)}。系统仅记录本系统内运行与查看，无法保证外部未看过数据。</p></div>`;}
target.innerHTML=resultHeader(id)+experiment+(r.metrics?`<div class="panel"><div class="panel-heading"><h3>净值与收益</h3><span class="badge ${d.request.synthetic||isReference(d.request)?'demo':''}">${d.request.synthetic?'合成演示 · 非投资结论':esc(modeNames[r.metadata?.price_model]||r.metadata?.price_model)}</span></div>${r.metadata?.reference_only?`<p class="callout demo">${esc(r.metadata.research_warning)}</p>`:''}${metricHtml(r.metrics)}<canvas class="chart" id="result-chart" aria-label="时间加权净值曲线"></canvas><p class="chart-caption">蓝色：策略时间加权净值${r.benchmark?'；橙色：相同期间与资金流基准':''}。入金不计为投资利润。</p><div class="result-meta"><span>成交 ${r.metrics.trades} 笔</span><span>年化收益 ${pct(r.metrics.annualized_return)}</span><span>波动率 ${pct(r.metrics.volatility)}</span><span>净入金 ${num(r.metrics.net_flows)}</span><span>净利润 ${num(r.metrics.net_profit)}</span></div><p class="help">${esc(r.metadata?.assumptions)}。${esc(r.metadata?.survivorship_bias)}。</p>${cashFlowPlanHtml(r.metadata)}${dataSourcesHtml(d.request)}<details><summary>复现元数据</summary><pre>${esc(JSON.stringify({run_id:id,snapshot:d.request.snapshot,strategy_hash:d.request.strategy_hash,engine_hash:d.request.engine_hash,version:d.request.app_version,git:d.request.git,metadata:r.metadata},null,2))}</pre></details></div><div class="panel"><div class="panel-heading"><h3>逐笔核对</h3><div class="result-tabs"><button data-table="trades">成交</button><button data-table="orders">信号与订单</button><button data-table="ledger">账户账本</button></div></div><div id="run-table" class="table-wrap"></div><div class="pagination"><button id="table-prev">上一页</button><span id="table-page"></span><button id="table-next">下一页</button></div></div>`:'<div class="panel">该窗口已跳过，查看热力图中的原因。</div>');
bindRunDetail(reveal);
document.querySelectorAll('[data-sample]').forEach(b=>b.onclick=safe(()=>openRun(id,Number(b.dataset.sample))));document.querySelectorAll('[data-section]').forEach(b=>b.onclick=safe(()=>openRun(id,0,b.dataset.section)));if(r.metrics){drawChart($('result-chart'),r.curve,r.benchmark?.curve);document.querySelectorAll('[data-table]').forEach(b=>b.onclick=safe(()=>{tableName=b.dataset.table;tableOffset=0;return loadTable();}));$('table-prev').onclick=safe(()=>{tableOffset=Math.max(0,tableOffset-100);return loadTable();});$('table-next').onclick=safe(()=>{tableOffset+=100;return loadTable();});await loadTable();if(token!==detailRequest)return;$('latest-preview').innerHTML=`<div class="panel-heading"><h3>最近一次研究</h3><span class="badge ${d.request.synthetic||isReference(d.request)?'demo':''}">${runDataLabel(d.request)}</span></div>${isReference(d.request)?`<p class="callout demo">${esc(referenceNote)}</p>`:''}${metricHtml(r.metrics)}<canvas class="chart" id="preview-chart" aria-label="最近研究曲线"></canvas><button id="show-latest">打开完整结果 →</button>`;$('show-latest').onclick=safe(async()=>{view('runs');await openRun(id);});requestAnimationFrame(()=>drawChart($('preview-chart'),r.curve));}}
async function loadTable(){const r=await api(`/runs/${currentRun}/table?table=${tableName}&offset=${tableOffset}&sample=${currentSample}&section=${currentSection}`);const keys=tableName==='trades'?['date','signal_date','symbol','quantity','price','fee','reason']:tableName==='orders'?['signal_date','symbol','quantity','reason','visible']:['date','type','symbol','amount','cash','debt','reason'];const labels={date:'日期',signal_date:'信号日期',symbol:'证券',quantity:'数量',price:'价格',fee:'费用',reason:'原因',visible:'可见数据摘要',type:'事件',amount:'金额',cash:'现金',debt:'借款'};$('run-table').innerHTML='<table><thead><tr>'+keys.map(k=>'<th>'+labels[k]+'</th>').join('')+'</tr></thead><tbody>'+r.items.map(row=>'<tr>'+keys.map(k=>`<td>${esc(typeof row[k]==='object'?JSON.stringify(row[k]):row[k])}</td>`).join('')+'</tr>').join('')+'</tbody></table>';$('table-page').textContent=`${r.total?tableOffset+1:0}–${Math.min(tableOffset+100,r.total)} / ${r.total}`;$('table-prev').disabled=tableOffset===0;$('table-next').disabled=tableOffset+100>=r.total;}
async function loadData(){await loadStatus();await loadOpenResources();const currentDatasets=await api('/datasets');$('dataset-cards').innerHTML=currentDatasets.map(d=>`<article class="panel"><div class="panel-heading"><h3>${esc(d.name)}</h3><span class="badge ${d.synthetic?'demo':''}">${d.synthetic?'合成':'真实来源'}</span></div><p>${esc(d.first)} → ${esc(d.last)}<br>${num(d.rows)} 条日线 · ${Object.keys(d.symbols).length} 个标的</p><div class="dataset-actions"><button data-coverage="${d.id}">检查覆盖与口径 →</button><button data-export-snapshot="${d.id}">导出快照数据包</button></div></article>`).join('');document.querySelectorAll('[data-coverage]').forEach(b=>b.onclick=safe(async()=>{const r=await api('/coverage/'+b.dataset.coverage);$('coverage-detail').innerHTML=`<div class="panel table-wrap"><table><thead><tr><th>证券</th><th>实际下载区间</th><th>行数</th><th>严格 VWAP</th><th>缺口 / 公司行动</th></tr></thead><tbody>${r.items.map(i=>`<tr><td>${esc(i.name)}<small>${esc(i.symbol)} · ${esc(i.currency)}</small></td><td>${esc(i.first)}<br>${esc(i.last)}</td><td>${i.rows}</td><td>${i.strict_vwap_rows} / ${i.rows}<small>候选均价：${i.candidate_vwap_rows||0} · 停牌：${i.suspended_rows||0}</small><small>${i.synthetic?'合成样本验证口径':'需逐标核实'}</small></td><td>${i.missing_sessions.length} 个日期缺口<small>公司行动：${i.actions_verified?'已标记验证':'未验证'} · 已导入现金事件 ${i.cash_action_count||0}</small><small>${esc(i.execution_blocked||'')}</small></td></tr>`).join('')}</tbody></table></div><details class="panel"><summary>来源与缺口详情</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`;}));document.querySelectorAll('[data-export-snapshot]').forEach(b=>b.onclick=safe(async()=>{b.disabled=true;try{const r=await fetch('/api/exports/'+encodeURIComponent(b.dataset.exportSnapshot));if(!r.ok){const problem=await r.json();throw new Error(problem.detail||'数据包导出失败');}const url=URL.createObjectURL(await r.blob()),link=document.createElement('a');link.href=url;link.download=`investment-lab-${b.dataset.exportSnapshot}.json.gz`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('数据包已下载到浏览器下载目录；文件包含快照行情和来源信息，不会自动上传。分享前请确认数据许可。');}finally{b.disabled=false;}}));filterDatasets();}
$('update-data').onclick=safe(async()=>{$('update-data').disabled=true;notice('正在检查交易日历、检查点和供应商配置。');try{const r=await api('/update',{});notice(r.message||`补数结束：新增 ${r.added}，修订 ${r.revised}，失败 ${r.failed}。`);await loadData();}finally{$('update-data').disabled=false;}});
async function loadUniverse(){const u=await api('/universe');instruments=u.instruments;renderUniverse();}
function renderUniverse(){const market=$('universe-market').value,query=$('universe-search').value.toLowerCase();const rows=instruments.filter(i=>(!market||i.market===market)&&(!query||(i.name+i.code).toLowerCase().includes(query)));$('universe-body').innerHTML=rows.map(i=>`<tr><td><b>${esc(i.code)}</b></td><td>${esc(i.name)}</td><td>${esc(i.market)} / ${esc(i.currency)}</td><td>${esc({stock:'股票',etf:'ETF',index:'指数',future:'期货',leveraged_etf:'杠杆ETF'}[i.kind])}</td><td><span class="badge">${i.mapping_verified?'映射已核实':'待验证映射'}</span></td><td><button data-instrument="${esc(i.id)}">编辑 · ${i.active?'启用':'停用'}</button></td></tr>`).join('');document.querySelectorAll('[data-instrument]').forEach(b=>b.onclick=()=>{$('instrument-json').value=JSON.stringify(instruments.find(i=>i.id===b.dataset.instrument),null,2);$('instrument-editor').open=true;$('instrument-editor').scrollIntoView({behavior:'smooth'});});}
$('universe-market').onchange=renderUniverse;$('universe-search').oninput=renderUniverse;
$('new-instrument').onclick=()=>{$('instrument-json').value=JSON.stringify({id:'US:NEW',code:'NEW',name:'新标的',market:'US',currency:'USD',kind:'stock',active:true,effective:new Date().toISOString().slice(0,10),listed:null,mapping_verified:false},null,2);$('instrument-editor').open=true;$('instrument-editor').scrollIntoView({behavior:'smooth'});};
$('save-instrument').onclick=safe(async()=>{await api('/universe',JSON.parse($('instrument-json').value));notice('股票池新版本已保存，历史数据和已有回测保持可追溯。');await loadUniverse();});
$('strategy-code').value='def initialize(ctx):\n    ctx.state["ordered"] = False\n\ndef on_session(ctx):\n    # ctx.history() 只返回当时可用的数据\n    if not ctx.state["ordered"]:\n        ctx.order_target_weight(ctx.symbols[0], 0.90, "初始买入")\n        ctx.state["ordered"] = True\n\ndef on_finish(result):\n    pass\n';
$('custom-files').onchange=safe(async()=>{if(!$('custom-files').value)return;const r=await api('/strategy?name='+encodeURIComponent($('custom-files').value));$('strategy-name').value=r.name;$('strategy-code').value=r.code;});
$('save-strategy').onclick=safe(async()=>{await api('/strategy',{name:$('strategy-name').value,code:$('strategy-code').value});notice('策略已保存，可在策略研究中选择运行。');await loadStrategies();});
async function loadBackups(){await loadStatus();const r=await api('/backups');$('backups-body').innerHTML=r.points.length?r.points.map(p=>`<tr><td>${esc(p.id)}<small>${esc(p.created)}</small></td><td>${esc(p.app_version)}</td><td><span class="badge good">已完成校验</span></td><td><button data-restore="${esc(p.id)}">选择恢复点</button></td></tr>`).join(''):'<tr><td colspan="4">尚无恢复点。点击建立一致性备份。</td></tr>';$('retention').textContent=`备份目录：${r.root}\n保留 ${r.retention.keep.length} 个恢复点，${r.retention.candidates.length} 个候选旧点；删除数量 0。`;document.querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>{$('restore-id').value=b.dataset.restore;$('restore-destination').focus();});}
$('create-backup').onclick=safe(async()=>{$('create-backup').disabled=true;try{const r=await api('/backups',{});notice(`备份已校验：${r.id}，${r.files} 个文件。`);await loadBackups();}finally{$('create-backup').disabled=false;}});
$('restore-backup').onclick=safe(async()=>{if(!$('restore-id').value||!$('restore-destination').value)throw new Error('先选择恢复点并填写不存在的独立目标目录。');const r=await api('/restore',{id:$('restore-id').value,destination:$('restore-destination').value});notice(`已恢复到 ${r.destination}；数据库检查 ${r.integrity}。当前运行目录未切换；按操作文档完成复现与切换。`);});
document.querySelectorAll('[data-doc]').forEach(b=>b.onclick=safe(async()=>{const d=await api('/docs/'+b.dataset.doc);$('doc-title').textContent=b.textContent;$('doc-text').textContent=d.text;$('doc-dialog').showModal();}));$('close-doc').onclick=()=>$('doc-dialog').close();
safe(async()=>{await Promise.all([loadStatus(),loadDatasets(),loadStrategies()]);initializeConfigPersistence();updateStrategyScopeNote();const runs=await api('/runs');const latest=runs.find(r=>r.status==='completed'&&r.kind==='single');if(latest){await openRun(latest.id,0,'holdout',false);}})();

// New submissions clear the prior selection so old results cannot be mistaken for a running task.
$('run-form').addEventListener('submit',()=>{$('result-detail').replaceChildren();});
const compareButton=document.createElement('button');compareButton.textContent='比较最近已完成的单次回测';$('refresh-runs').parentElement.appendChild(compareButton);
compareButton.onclick=safe(async()=>{const rs=(await api('/runs?limit=50')).filter(r=>r.status==='completed'&&r.kind==='single').slice(0,12);$('result-detail').innerHTML=`<div class="panel"><h3>结果比较</h3><p class="help">按各次原始区间显示；先核对时期与资金流再比较收益。不同币种的权益不相加；合成与参考研究结果不进入正常排名。</p><div class="table-wrap"><table><thead><tr><th>运行 / 策略</th><th>区间</th><th>收益</th><th>最大回撤</th><th>净入金</th><th>费用</th><th>数据</th></tr></thead><tbody>${rs.map(r=>`<tr><td>${esc(r.id.slice(0,10))}<small>${esc(r.config.strategy)}</small></td><td>${esc(r.config.config.start)}<br>${esc(r.config.config.end)}</td><td>${pct(r.summary?.total_return)}</td><td>${pct(r.summary?.max_drawdown)}</td><td>${num(r.summary?.net_flows)}</td><td>${num(r.summary?.fees)}</td><td>${runDataLabel(r.config)}<small>${esc(modeNames[r.config.config.mode]||r.config.config.mode)}</small>${isReference(r.config)?'<small>不含股息 · 不进入正常排名</small>':''}</td></tr>`).join('')}</tbody></table></div></div>`;});
// Render additional saved account series without downloading trade/ledger history.
const detailObserver=new MutationObserver(()=>{const chart=$('result-chart');if(!chart||$('series-select'))return;const label=document.createElement('label');label.textContent='显示账户曲线';const select=document.createElement('select');select.id='series-select';select.innerHTML='<option value="twr_index">时间加权净值</option><option value="equity">权益</option><option value="drawdown">回撤</option><option value="leverage">实际杠杆</option><option value="debt">借款余额</option><option value="margin">保证金占用</option><option value="interest">累计融资利息</option>';label.appendChild(select);chart.before(label);select.onchange=safe(async()=>{const d=await api(`/runs/${currentRun}?sample=${currentSample}&section=${currentSection}`);const field=select.value;drawChart(chart,d.result.curve.map(r=>({...r,twr_index:Number(r[field])})),field==='twr_index'?d.result.benchmark?.curve:null);});});detailObserver.observe($('result-detail'),{childList:true});

// Canvas needs a visible layout box; redraw when its panel becomes visible or changes size.
const renderChart=drawChart, chartSeries=new Map();
const chartResize=new ResizeObserver(entries=>{for(const e of entries){const saved=chartSeries.get(e.target.id);if(saved&&saved.canvas===e.target&&e.contentRect.width>0&&e.contentRect.height>0)renderChart(saved.canvas,saved.curve,saved.benchmark);}});
drawChart=(canvas,curve,benchmark)=>{if(!canvas||!curve?.length)return;const previous=chartSeries.get(canvas.id);if(previous&&previous.canvas!==canvas)chartResize.unobserve(previous.canvas);chartSeries.set(canvas.id,{canvas,curve,benchmark});chartResize.observe(canvas);if(canvas.getBoundingClientRect().width>0)renderChart(canvas,curve,benchmark);};

let openPoll=null;
async function loadOpenResources(){
 const r=await api('/open-resources'),items=r.report?.items||[],ok=items.filter(i=>i.status==='downloaded');
 const rows=ok.reduce((a,i)=>a+(i.coverage?.rows||0),0),candidates=ok.reduce((a,i)=>a+(i.coverage?.candidate_vwap_rows||0),0);
 $('open-progress').textContent=`${r.active?'正在后台下载 · ':''}${ok.length} 个标的已下载 / ${items.length} 个已处理 · ${num(rows)} 条行情 · ${num(candidates)} 条候选均价 · ${items.filter(i=>i.status==='failed').length} 个失败 / ${items.filter(i=>i.status==='deferred').length} 个延后。${r.report?'详细缺口请打开数据集覆盖报告。':'尚未运行公开资源下载。'}`;
 $('open-samples').disabled=r.active;$('open-universe').disabled=r.active;
 const sources=r.catalog.resources||[];
 $('open-catalog').innerHTML='<table><thead><tr><th>资源</th><th>本项目用途</th><th>接入状态 / 限制</th></tr></thead><tbody>'+sources.map(s=>`<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a><small>${esc(s.code_license||'官方公开数据')}</small></td><td>${esc(s.use)}</td><td>${esc(s.status)}<small>${esc(s.limits)}</small></td></tr>`).join('')+'</tbody></table>';
 if(openPoll){clearTimeout(openPoll);openPoll=null;}if(r.active)openPoll=setTimeout(safe(loadOpenResources),3000);
}
$('open-samples').onclick=safe(async()=>{await api('/open-resources',{scope:'sample'});notice('公开样本下载已在后台启动。');await loadOpenResources();});
$('open-universe').onclick=safe(async()=>{await api('/open-resources',{scope:'universe'});notice('完整股票池下载已在后台启动，进度逐个保存。');await loadOpenResources();});
$('open-refresh').onclick=safe(loadData);
function filterDatasets(){const q=$('data-filter').value.trim().toLowerCase();document.querySelectorAll('#dataset-cards article').forEach(e=>e.hidden=!e.textContent.toLowerCase().includes(q));}
$('data-filter').oninput=filterDatasets;
$('snapshot-import-file').onchange=()=>{$('snapshot-import').disabled=!$('snapshot-import-file').files?.length;};
$('snapshot-import').onclick=async()=>{const file=$('snapshot-import-file').files?.[0];if(!file)return;const button=$('snapshot-import'),status=$('snapshot-import-status');button.disabled=true;status.textContent='正在解压并核对数据包…';status.setAttribute('aria-busy','true');try{const packageData=await PortableDataPackage.readGzipJson(file);const imported=await api('/imports',packageData);status.textContent=`导入完成：${imported.name}，${num(imported.rows)} 条行情。新快照 ${imported.snapshot.slice(0,12)}；原始响应不包含在数据包内。`;notice('数据包已校验并导入本机数据目录。');$('snapshot-import-file').value='';await loadData();}catch(error){status.textContent=`导入失败：${error.message}`;notice(error.message,true);}finally{status.removeAttribute('aria-busy');button.disabled=!$('snapshot-import-file').files?.length;}};
