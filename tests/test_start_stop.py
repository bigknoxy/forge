import os
import subprocess
import sys
import tempfile
import time

REPO_DIR = os.path.abspath(os.path.dirname(__file__) + '/..')
FORGE_CLI = os.path.join(REPO_DIR, 'forge_cli.py')


def _run_cli(args, env=None):
    cmd = [sys.executable, FORGE_CLI] + args
    e = os.environ.copy()
    if env:
        e.update(env)
    res = subprocess.run(cmd, capture_output=True, text=True, env=e)
    return res


def test_start_and_stop_fallback():
    td = tempfile.TemporaryDirectory()
    work = td.name
    bin_dir = os.path.join(work, 'bin')
    os.makedirs(bin_dir, exist_ok=True)
    # create fake uvicorn that sleeps; it should be executable
    uv = os.path.join(bin_dir, 'uvicorn')
    with open(uv, 'w') as f:
        f.write('#!/bin/sh\n')
        f.write('echo fake-uvicorn-start $$ > "' + work + '/forge_fakepid.txt"\n')
        f.write('sleep 60\n')
    os.chmod(uv, 0o755)

    # Intentionally set PATH to only our bin_dir so systemctl is not found
    env = {'WORKSPACE_DIR': work, 'PATH': bin_dir }
    # Ensure repo root on sys.path for module resolution in subprocess
    env['PYTHONPATH'] = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    # ensure any existing pidfile removed
    pidfile = os.path.join(work, 'forge.pid')
    try:
        if os.path.exists(pidfile):
            os.remove(pidfile)
    except Exception:
        pass

    # Start (should fall back to fake uvicorn since systemctl likely absent in test env)
    res = _run_cli(['start'], env=env)
    assert res.returncode == 0
    # pidfile should exist
    ppath = os.path.join(work, 'forge.pid')
    assert os.path.exists(ppath)
    with open(ppath) as f:
        pid = f.read().strip()
    assert pid.isdigit()

    # process should be running
    time.sleep(0.5)
    # stop
    res2 = _run_cli(['stop'], env=env)
    assert res2.returncode == 0
    # pidfile should be removed
    assert not os.path.exists(ppath)

    td.cleanup()
