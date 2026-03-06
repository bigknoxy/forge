import os
import sys
import tempfile
import time
import subprocess

WRAPPER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'forge_start.py'))

def test_wrapper_writes_pidfile_and_cleans_up():
    td = tempfile.TemporaryDirectory()
    work = td.name
    # create fake uvicorn in PATH
    bin_dir = os.path.join(work, 'bin')
    os.makedirs(bin_dir, exist_ok=True)
    uv = os.path.join(bin_dir, 'uvicorn')
    with open(uv, 'w') as f:
        f.write('#!/bin/sh\n')
        f.write('echo fake-uvicorn-start $$ > "' + work + '/fakepid.txt"\n')
        f.write('sleep 60\n')
    os.chmod(uv, 0o755)

    env = os.environ.copy()
    env['PATH'] = bin_dir + os.pathsep + env.get('PATH','')
    # point runtime dir to temp so we don't need root
    env['FORGE_RUNTIME_DIR'] = work
    env['WORKSPACE_DIR'] = work

    # start wrapper
    p = subprocess.Popen([sys.executable, WRAPPER], env=env)
    time.sleep(0.5)
    pidfile = os.path.join(work, 'forge.pid')
    assert os.path.exists(pidfile)
    with open(pidfile) as f:
        pid = f.read().strip()
    assert pid.isdigit()
    # now terminate wrapper (send SIGTERM)
    p.terminate()
    p.wait(timeout=5)
    # pidfile should be removed
    assert not os.path.exists(pidfile)
    td.cleanup()
