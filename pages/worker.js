import {loadPyodide} from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const PYODIDE_BASE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
const SOURCE_FILES = [
  '__init__.py', 'common.py',
  'engine/__init__.py', 'engine/account.py', 'engine/models.py', 'engine/cash_flows.py', 'engine/reference.py', 'engine/core.py',
  'data/__init__.py', 'data/compose.py', 'data/validation.py',
  'research/__init__.py', 'research/metrics.py', 'research/experiments.py',
  'strategies/__init__.py', 'strategies/examples.py', 'strategies/catalog.json',
];

let runtimePromise;
let pyodide;
let numpyLoaded = false;
let messageQueue = Promise.resolve();
let loadedManifestHash;

async function sha256Hex(value) {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function runtime(expectedManifestHash = null) {
  if (!runtimePromise) {
    runtimePromise = (async () => {
      pyodide = await loadPyodide({indexURL: PYODIDE_BASE});
      const manifestResponse = await fetch(new URL('./runtime-manifest.json', import.meta.url), {cache:'no-store'});
      if (!manifestResponse.ok) throw new Error(`无法读取浏览器计算版本清单（${manifestResponse.status}）`);
      const manifestText = await manifestResponse.text();
      const manifest = JSON.parse(manifestText);
      loadedManifestHash = await sha256Hex(new TextEncoder().encode(manifestText));
      pyodide.FS.mkdirTree('/runtime/investment_lab');
      for (const relative of SOURCE_FILES) {
        const sourceUrl = new URL(`./runtime/investment_lab/${relative}`, import.meta.url);
        const response = await fetch(sourceUrl, {cache:'no-store'});
        if (!response.ok) throw new Error(`无法读取浏览器计算模块 ${relative}（${response.status}）`);
        const source = new Uint8Array(await response.arrayBuffer());
        const manifestPath = `runtime/investment_lab/${relative}`;
        const expectedHash = manifest.files?.[manifestPath];
        if (!expectedHash || await sha256Hex(source) !== expectedHash) throw new Error(`浏览器计算模块版本不匹配：${relative}；请刷新页面。`);
        const target = `/runtime/investment_lab/${relative}`;
        const parent = target.slice(0, target.lastIndexOf('/'));
        pyodide.FS.mkdirTree(parent);
        pyodide.FS.writeFile(target, source);
      }
      pyodide.runPython("import sys; sys.path.insert(0, '/runtime')");
      return pyodide;
    })().catch(error => {
      runtimePromise = null;
      throw error;
    });
  }
  const instance = await runtimePromise;
  if (expectedManifestHash && expectedManifestHash !== loadedManifestHash) throw new Error('当前页面与计算引擎版本不一致；请刷新页面后重试。');
  return instance;
}

const VERIFY_CODE = `
import json, re
from investment_lab.common import digest
from investment_lab.data.validation import validate_dataset

_CREDENTIAL_FIELDS = {"token", "api_token", "api_key", "access_token", "secret", "secret_key", "password", "authorization", "cookie"}
_CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)\\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\\b"),
    re.compile(r"\\b(?:AKIA|ASIA)[0-9A-Z]{16}\\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\\b(?:api[_-]?token|access[_-]?token)\\s*[=:]\\s*[^&\\s]{20,}"),
]
def _contains_credentials(value):
    if isinstance(value, dict):
        return any(str(k).lower() in _CREDENTIAL_FIELDS or _contains_credentials(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_credentials(v) for v in value)
    return isinstance(value, str) and any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS)

package = json.loads(package_json)
if package.get('format') != 'investment-lab-snapshot' or type(package.get('format_version')) is not int or package['format_version'] != 1:
    raise ValueError('不支持的数据包格式或版本。')
manifest, bars, snapshot = package.get('manifest'), package.get('bars'), package.get('snapshot_id')
if not isinstance(manifest, dict) or not isinstance(bars, list) or not isinstance(snapshot, str):
    raise ValueError('数据包缺少快照清单、行情或原始编号。')
if not re.fullmatch(r'[0-9a-f]{64}', snapshot) or digest(manifest) != snapshot:
    raise ValueError('快照清单摘要不匹配；数据包可能损坏或被修改。')
if manifest.get('composition'):
    raise ValueError('请分别导入组合快照中的原始来源数据包。')
if _contains_credentials(package):
    raise ValueError('数据包疑似包含凭据，已拒绝导入。')
if type(manifest.get('schema')) is not int or manifest.get('schema') != 1:
    raise ValueError('不支持的快照清单版本。')
for key in ('name', 'securities', 'sessions', 'synthetic', 'partitions'):
    if key not in manifest:
        raise ValueError('快照清单缺少必要字段。')
if not isinstance(manifest['name'], str) or not manifest['name'].strip() or len(manifest['name']) > 200:
    raise ValueError('快照名称无效。')
if not isinstance(manifest['securities'], dict) or not manifest['securities'] or not isinstance(manifest['sessions'], list):
    raise ValueError('快照清单缺少证券或交易日。')
if any(not isinstance(day, str) for day in manifest['sessions']) or not manifest['sessions']:
    raise ValueError('快照交易日字段无效。')
if type(manifest['synthetic']) is not bool or not isinstance(manifest.get('partitions'), list):
    raise ValueError('快照版本或数据分区字段无效。')
if not isinstance(manifest.get('source', {}), dict) or not isinstance(manifest.get('actions', []), list):
    raise ValueError('来源或公司行动字段格式无效。')
if any(not isinstance(action, dict) for action in manifest.get('actions', [])):
    raise ValueError('公司行动格式无效。')
if any(not isinstance(symbol, str) or not isinstance(security, dict) or
       any(not isinstance(security.get(field), str) or not security.get(field) for field in ('market','currency','timezone'))
       for symbol, security in manifest['securities'].items()):
    raise ValueError('证券缺少市场、币种或时区信息。')
if not bars or any(not isinstance(row, dict) or not isinstance(row.get('symbol'), str) or not isinstance(row.get('date'), str) for row in bars):
    raise ValueError('行情行格式无效。')
errors = validate_dataset(manifest['securities'], bars, manifest.get('actions', []), manifest['sessions'], manifest['synthetic'])
if errors:
    raise ValueError('行情校验失败：' + '；'.join(errors[:6]))
json.dumps({'snapshot_id': snapshot, 'name': manifest['name'], 'synthetic': manifest['synthetic'], 'rows': len(bars), 'source': manifest.get('source', {}), 'securities': manifest['securities'], 'sessions': manifest['sessions']}, ensure_ascii=False)
`;

const RUN_CODE = `
import json
from dataclasses import replace
from js import postMessage
from types import SimpleNamespace
from investment_lab.common import digest
from investment_lab.data.compose import compose_portable_manifests, validate_composed_selection
from investment_lab.engine.core import simulate
from investment_lab.engine.models import Config
from investment_lab.research.experiments import benchmark_result, holdout, rolling
from investment_lab.strategies.examples import Builtin, NAMES, validate_params

request = json.loads(run_json)
packages = [json.loads(item) for item in request['packages']]
for package in packages:
    if digest(package['manifest']) != package['snapshot_id']:
        raise ValueError('已保存的数据包摘要校验失败；请重新导入原文件。')
if len(packages) == 1:
    manifest = packages[0]['manifest']
    source_ids = [packages[0]['snapshot_id']]
else:
    source_ids = sorted(item['snapshot_id'] for item in packages)
    by_id = {item['snapshot_id']: item for item in packages}
    _, manifest = compose_portable_manifests([(sid, by_id[sid]['manifest']) for sid in source_ids])
bars = [row for package in packages for row in package['bars']]
config = Config(**request['config'])
validate_composed_selection(manifest, config)
strategy_name = request['strategy']
if strategy_name not in NAMES:
    raise ValueError('浏览器版只支持内置策略。')
params = request.get('params', {})
validate_params(strategy_name, params, config.symbols)
research = request.get('research', {})
run_id = request['run_id']
def factory():
    return Builtin(strategy_name)
def report_progress(done, total):
    postMessage(json.dumps({'type': 'progress', 'requestId': run_id, 'done': int(done), 'total': int(total)}))

if request['kind'] == 'single':
    session_count = sum(config.start <= day <= config.end for day in manifest['sessions'])
    total_work = session_count * (2 if config.benchmark else 1)
    result = simulate(manifest, bars, config, factory(), params, lambda done, _total: report_progress(done, total_work))
    if config.benchmark:
        result['benchmark'] = benchmark_result(manifest, bars, config, lambda done, _total: report_progress(session_count + done, total_work))
    else:
        result['benchmark'] = None
elif request['kind'] == 'rolling':
    result = rolling(manifest, bars, config, factory, params, progress=report_progress, **research)
elif request['kind'] == 'holdout':
    result = holdout(manifest, bars, config, factory, params, progress=report_progress, **research)
else:
    raise ValueError('研究方式无效。')
result['run_id'] = run_id
result['data_snapshots'] = source_ids
result['result_hash'] = digest(result)
json.dumps(result, ensure_ascii=False, default=str, allow_nan=False)
`;

function reply(requestId, payload) {
  self.postMessage(JSON.stringify({requestId, ...payload}));
}

async function processMessage(data) {
  const {requestId, type} = data || {};
  try {
    const py = await runtime(data?.request?.engine_manifest_hash || null);
    if (type === 'verify') {
      py.globals.set('package_json', data.packageJson);
      const value = JSON.parse(py.runPython(VERIFY_CODE));
      reply(requestId, {type: 'verified', value, pyodide: py.version});
      return;
    }
    if (type === 'demo') {
      const value = JSON.parse(py.runPython(`
import json
from investment_lab.common import digest
sessions = ['2024-01-04', '2024-01-05', '2024-01-08', '2024-01-09', '2024-01-10']
security = {'name':'五日合成样本', 'market':'US', 'currency':'USD', 'timezone':'America/New_York', 'kind':'stock', 'lot':1,
            'listed':sessions[0], 'listing_verified':True, 'actions_verified':True, 'margin_eligible':True}
bars = [{'symbol':'DEMO', 'date':day, 'open':'10', 'high':'11', 'low':'9', 'close':'10', 'volume':'100000',
         'amount':'1000000', 'vwap_value':'10', 'vwap_method':'amount_volume_verified', 'vwap_session':'full',
         'quality_status':'verified', 'source':'synthetic-demo', 'available_at':day+'T22:00:00+00:00', 'status':'trading'} for day in sessions]
manifest = {'schema':1, 'name':'五日合成演示 · 仅流程验证', 'securities':{'DEMO':security}, 'partitions':[],
            'actions':[], 'sessions':sessions, 'source':{'provider':'synthetic-demo'}, 'synthetic':True, 'raw':None}
package = {'format':'investment-lab-snapshot', 'format_version':1, 'manifest':manifest, 'bars':bars}
package['snapshot_id'] = digest(manifest)
json.dumps(package, ensure_ascii=False)
`));
      reply(requestId, {type: 'demo', packageJson: JSON.stringify(value), pyodide: py.version});
      return;
    }
    if (type === 'run') {
      if (!numpyLoaded) {
        await py.loadPackage(['numpy', 'tzdata']);
        numpyLoaded = true;
      }
      py.globals.set('run_json', JSON.stringify(data.request));
      const value = JSON.parse(py.runPython(RUN_CODE));
      reply(requestId, {type: 'result', value, pyodide: py.version});
      return;
    }
    throw new Error('未知的浏览器计算任务。');
  } catch (error) {
    reply(requestId, {type: 'error', error: String(error?.message || error), detail: String(error?.stack || '')});
  }
}

self.addEventListener('message', event => {
  messageQueue = messageQueue.then(() => processMessage(event.data), () => processMessage(event.data));
});
