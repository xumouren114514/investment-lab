import importlib.util
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location('desktop_app', Path(__file__).resolve().parents[1] / 'scripts/desktop_app.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


@pytest.fixture
def local_server(tmp_path):
    state = {'payload': {'version': '0.2.2', 'project_path': str(desktop.PROJECT), 'data_path': str(tmp_path)}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(state.get('code', 200))
            if state.get('code') == 302:
                self.send_header('Location', '/somewhere-else')
            self.end_headers()
            self.wfile.write(json.dumps(state['payload']).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, state, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_probe_accepts_matching_local_service_without_system_proxy(local_server, monkeypatch):
    port, state, data = local_server
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    assert desktop.probe_service(data, port) == state['payload']


def test_unused_windows_port_is_detected_without_connect_timeout(tmp_path, monkeypatch):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    monkeypatch.setattr(desktop.socket, 'create_connection', lambda *_a, **_kw: pytest.fail('do not infer occupancy from connect timeout'))
    assert desktop.probe_service(tmp_path, port) is None


@pytest.mark.parametrize('change', ['data', 'project', 'invalid_json_type', 'redirect'])
def test_probe_rejects_unrelated_service_and_redirect(local_server, change):
    port, state, data = local_server
    if change == 'data':
        state['payload']['data_path'] = str(data / 'other')
    elif change == 'project':
        state['payload']['project_path'] = str(data)
    elif change == 'invalid_json_type':
        state['payload'] = []
    else:
        state['code'] = 302
    with pytest.raises(desktop.LaunchError, match='端口.*已有服务'):
        desktop.probe_service(data, port)


def test_reuses_ready_service_without_spawning(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'probe_service', lambda *_: {'version': 'test'})
    monkeypatch.setattr(desktop, 'spawn_server', lambda *_: pytest.fail('must not spawn a duplicate'))
    assert desktop.ensure_service(tmp_path)['reused']


def test_concurrent_shortcut_clicks_start_only_one_server(tmp_path, monkeypatch):
    state = {'ready': False, 'starts': 0}

    def spawn(*_):
        state['starts'] += 1
        state['ready'] = True
        return SimpleNamespace(pid=123, poll=lambda: None)

    monkeypatch.setattr(desktop, 'probe_service', lambda *_: {'version': 'test'} if state['ready'] else None)
    monkeypatch.setattr(desktop, 'spawn_server', spawn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: desktop.ensure_service(tmp_path), range(2)))
    assert state['starts'] == 1
    assert sorted(result['reused'] for result in results) == [False, True]
    assert json.loads((tmp_path / 'state/desktop-service.json').read_text())['launcher_child_pid'] == 123


def test_start_failure_exposes_exit_code_and_log(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'probe_service', lambda *_: None)

    def fail(data, port, log, project):
        log.write_text('具体原因：dependency missing', encoding='utf-8')
        return SimpleNamespace(pid=123, returncode=7, poll=lambda: 7)

    monkeypatch.setattr(desktop, 'spawn_server', fail)
    with pytest.raises(desktop.LaunchError, match='退出码 7.*\n具体原因：dependency missing'):
        desktop.ensure_service(tmp_path)


def test_start_timeout_cleans_only_the_new_process(tmp_path, monkeypatch):
    import psutil
    ticks = iter([0, 2])
    terminated = []
    child = SimpleNamespace(terminate=lambda: terminated.append('child'))
    parent = SimpleNamespace(children=lambda **_: [child], terminate=lambda: terminated.append('parent'))
    monkeypatch.setattr(desktop.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(desktop, 'probe_service', lambda *_: None)
    monkeypatch.setattr(desktop, 'spawn_server', lambda *_: SimpleNamespace(pid=123, poll=lambda: None))
    monkeypatch.setattr(psutil, 'Process', lambda pid: parent if pid == 123 else pytest.fail('wrong process'))
    monkeypatch.setattr(psutil, 'wait_procs', lambda processes, timeout: (processes, []))
    # The lock uses the same time module; isolate it from the fake deadline clock.
    from contextlib import nullcontext
    monkeypatch.setattr(desktop, 'file_lock', lambda *_, **__: nullcontext())
    with pytest.raises(desktop.LaunchError, match='等待服务启动超过'):
        desktop.ensure_service(tmp_path, timeout=1)
    assert terminated == ['child', 'parent']


def test_application_window_uses_separate_profile_and_literal_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Profile With Spaces'))
    command = desktop.browser_command(Path('C:/Program Files/Edge/msedge.exe'), 'http://127.0.0.1:8765/')
    assert command[0] == str(Path('C:/Program Files/Edge/msedge.exe'))
    assert '--app=http://127.0.0.1:8765/' in command
    assert f'--user-data-dir={tmp_path / "Profile With Spaces/InvestmentLab/browser-profile"}' in command
    assert not any('remote-debugging' in item or 'disable-web-security' in item for item in command)
