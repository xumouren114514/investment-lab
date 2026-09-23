import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const EXPECTED_DIGEST = 'efa17a097b1d7087ec3cf34d0f4270dd52068591e3100f0d85fc2fc1a551dfc3';
const SOURCE_FILES = [
  '__init__.py', 'common.py',
  'engine/__init__.py', 'engine/account.py', 'engine/models.py', 'engine/cash_flows.py', 'engine/reference.py', 'engine/core.py',
  'research/__init__.py', 'research/metrics.py',
  'data/__init__.py', 'data/compose.py',
];

self.onmessage = async () => {
  try {
    const pyodide = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/' });
    await pyodide.loadPackage(['numpy', 'tzdata']);
    const packageRoot = '/site/investment_lab';
    pyodide.FS.mkdirTree(packageRoot);
    for (const relative of SOURCE_FILES) {
      const response = await fetch(`/src/investment_lab/${relative}`);
      if (!response.ok) throw new Error(`无法读取本地引擎模块 ${relative} (${response.status})`);
      const target = `${packageRoot}/${relative}`;
      const parent = target.slice(0, target.lastIndexOf('/'));
      pyodide.FS.mkdirTree(parent);
      pyodide.FS.writeFile(target, new Uint8Array(await response.arrayBuffer()));
    }
    const raw = pyodide.runPython(`
import json, sys
sys.path.insert(0, '/site')
from types import SimpleNamespace
from investment_lab.common import digest
from investment_lab.engine.core import simulate
from investment_lab.engine.models import Config

sessions = ['2024-01-04', '2024-01-05', '2024-01-08', '2024-01-09', '2024-01-10']
security = {'name': '手算样本', 'market': 'US', 'currency': 'USD', 'timezone': 'America/New_York',
            'kind': 'stock', 'lot': 1, 'listed': '2024-01-04', 'listing_verified': True,
            'actions_verified': True, 'margin_eligible': True}
bars = [{'symbol': 'A', 'date': day, 'open': '10', 'high': '11', 'low': '9', 'close': '10',
         'volume': '100000', 'amount': '1000000', 'vwap_value': '10', 'vwap_method': 'amount_volume_verified',
         'vwap_session': 'full', 'quality_status': 'verified', 'source': 'test',
         'available_at': day + 'T22:00:00+00:00', 'status': 'trading'} for day in sessions]
manifest = {'name': 'test', 'securities': {'A': security}, 'sessions': sessions, 'actions': [],
            'synthetic': True, 'source': {'provider': 'synthetic'}}
config = Config(start=sessions[0], end=sessions[-1], symbols=['A'], initial_cash='1000',
                commission_bps='0', slippage_bps='0', max_participation='1')
def on_session(ctx):
    if not ctx.state.get('sent'):
        ctx.order_shares('A', 50, 'hand calculation')
        ctx.state['sent'] = True
result = simulate(manifest, bars, config, SimpleNamespace(on_session=on_session))
json.dumps({'digest': digest(result), 'trades': len(result['trades']),
            'final_equity': result['metrics']['final_equity'], 'ending_cash': result['curve'][-1]['cash']})
    `);
    const output = JSON.parse(raw);
    const passed = output.digest === EXPECTED_DIGEST && output.trades === 1 &&
      output.final_equity === 1000 && output.ending_cash === '500.00';
    self.postMessage({ status: passed ? 'passed' : 'failed', pyodide: pyodide.version, ...output, expected_digest: EXPECTED_DIGEST });
  } catch (error) {
    self.postMessage({ status: 'failed', error: String(error?.stack || error) });
  }
};
