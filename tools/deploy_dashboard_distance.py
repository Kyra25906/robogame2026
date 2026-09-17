"""部署包内运行；默认只检查，--apply 备份、更新并重启网页，不启动运动。"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(path, body=None):
    req = Request('http://127.0.0.1:8765' + path,
                  data=None if body is None else json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def validate_files(root, payload, manifest):
    for name, expected in manifest['payload'].items():
        source = (payload / name).resolve()
        target = (root / name).resolve()
        if payload.resolve() not in source.parents or root not in target.parents:
            raise RuntimeError('Path outside package/project: ' + name)
        if digest(source) != expected:
            raise RuntimeError('Package checksum mismatch: ' + name)
        if name in manifest['baseline']:
            if not target.exists() or digest(target) != manifest['baseline'][name]:
                raise RuntimeError('Live file changed since review; inspect before deploying: ' + name)
        elif target.exists():
            raise RuntimeError('New target already exists; inspect before deploying: ' + name)


def ensure_idle(snapshot):
    if snapshot['mode'] != 'OBSERVE' or snapshot.get('mechanism_busy'):
        raise RuntimeError('Release manual control / stop active tasks before deployment')
    telemetry = snapshot['telemetry']
    for key in ('velocity_vx', 'velocity_vy', 'velocity_wz'):
        value, age = telemetry.get(key), telemetry.get(key + '_age_s')
        if (not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > .005
                or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= .5):
            raise RuntimeError('Robot is moving or velocity feedback is missing/stale')
    if any(p['running'] and p['name'] in ('motion', 'arm', 'line') for p in snapshot['processes']):
        raise RuntimeError('Stop motion/arm/line processes before deployment')


def restore_files(root, backup, manifest):
    # 新增文件保留但旧版入口不会引用；恢复所有被替换文件。
    for name in manifest['payload']:
        saved = backup / name
        if saved.exists():
            shutil.copy2(saved, root / name)


def launch(root, env, logfile):
    with logfile.open('ab') as log:
        return subprocess.Popen([sys.executable, 'tools/field_dashboard.py', '--host', '0.0.0.0'],
                                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/home/rg26/robogame'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    bundle = Path(__file__).resolve().parent
    manifest = json.loads((bundle / 'manifest.json').read_text())
    payload = bundle / 'payload'
    validate_files(root, payload, manifest)
    # 在隔离目录测试待部署源码，使用现场 core 的真实配置。
    check = bundle / 'check'
    check.mkdir(exist_ok=True)
    shutil.copytree(payload / 'tools', check / 'tools', dirs_exist_ok=True)
    for name in ('field_console.py', 'field_dashboard_core.py'):
        shutil.copy2(root / 'tools' / name, check / 'tools' / name)
    shutil.copytree(bundle / 'tests', check / 'tests', dirs_exist_ok=True)
    subprocess.run([sys.executable, '-B', '-m', 'unittest', 'tests.test_dashboard_distance',
                    'tests.test_field_dashboard_mechanism', 'tests.test_dashboard_line', '-q'],
                   cwd=check, check=True)
    snapshot = api('/api/snapshot')
    ensure_idle(snapshot)
    processes = snapshot['processes']
    candidates = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            if b'tools/field_dashboard.py' in argv and (proc / 'cwd').resolve() == root:
                candidates.append(proc)
        except (OSError, PermissionError):
            pass
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one dashboard process, found ' + str(len(candidates)))
    proc = candidates[0]
    env = dict(item.decode().split('=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
    print('Preflight passed; PID=' + proc.name, flush=True)
    if not args.apply:
        print('Read-only check complete. Use --apply to deploy.')
        return
    backup = root / 'outputs' / 'dashboard_backups' / time.strftime('%Y%m%d_%H%M%S')
    backup.mkdir(parents=True, exist_ok=False)
    for name in manifest['payload']:
        target = root / name
        if target.exists():
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / name)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('Backup: ' + str(backup), flush=True)
    for p in processes:
        if p['running']:
            api('/api/process/' + p['name'] + '/stop', {})
    os.kill(int(proc.name), signal.SIGTERM)
    for _ in range(50):
        if not proc.exists():
            break
        time.sleep(.1)
    if proc.exists():
        # 旧服务的 SSE 长连接可能阻塞退出；管理的子进程已通过 API 停止。
        argv = (proc / 'cmdline').read_bytes().split(b'\0')
        if b'tools/field_dashboard.py' not in argv:
            raise RuntimeError('PID changed; refusing to stop another process')
        os.kill(int(proc.name), signal.SIGKILL)
    child = None
    try:
        for name in manifest['payload']:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + '.deploy-new')
            shutil.copy2(payload / name, staged)
            staged.replace(target)
        child = launch(root, env, backup / 'new-service.log')
        print('New PID=' + str(child.pid), flush=True)
        for _ in range(30):
            time.sleep(.5)
            if child.poll() is not None:
                raise RuntimeError('New service exited. See ' + str(backup / 'new-service.log'))
            try:
                snapshot = api('/api/snapshot')
                if 'distance_result' in snapshot and snapshot['ros_error'] is None:
                    break
            except OSError:
                continue
        else:
            raise RuntimeError('New service did not become ready. Backup: ' + str(backup))
    except Exception:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        restore_files(root, backup, manifest)
        restored = launch(root, env, backup / 'rollback-service.log')
        print('ROLLED BACK files; old service launch PID=' + str(restored.pid)
              + '. Check browser; bridge/localization may need starting.', flush=True)
        raise
    for p in processes:
        if p['running'] and p['name'] in ('bridge', 'localization'):
            api('/api/process/' + p['name'] + '/start', {})
    print('DEPLOYED: refresh browser. No movement task started.', flush=True)


if __name__ == '__main__':
    main()
