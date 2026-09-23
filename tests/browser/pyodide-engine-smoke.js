const result = document.querySelector('#result');
const button = document.querySelector('#run');

button.addEventListener('click', async () => {
  button.disabled = true;
  result.textContent = '正在加载 Pyodide 与固定时区/数值依赖…';
  const worker = new Worker('./pyodide-engine-smoke-worker.js', { type: 'module' });
  worker.onmessage = event => {
    const value = event.data;
    result.textContent = JSON.stringify(value, null, 2);
    worker.terminate();
    button.disabled = false;
    if (value.status !== 'passed') result.dataset.status = 'failed';
    else result.dataset.status = 'passed';
  };
  worker.onerror = event => {
    result.textContent = `Worker 失败：${event.message}`;
    result.dataset.status = 'failed';
    worker.terminate();
    button.disabled = false;
  };
  worker.postMessage({ run: true });
});
