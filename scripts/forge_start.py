#!/usr/bin/env python3
"""Start wrapper for Forge Uvicorn process.

Behaviors:
- Launches the uvicorn executable (prefers /opt/forge-venv/bin/uvicorn, else PATH)
- Writes a pidfile to FORGE_RUNTIME_DIR/forge.pid (default /var/run/forge). The dir can be overridden by FORGE_RUNTIME_DIR env var.
- Forwards SIGTERM/SIGINT to the child process group and ensures pidfile cleanup.

This script is intended to be installed at /opt/forge/bin/forge-start and used as systemd ExecStart.
"""

import os
import sys
import shutil
import signal
import subprocess
import time

RUNTIME_DIR = os.getenv('FORGE_RUNTIME_DIR', '/var/run/forge')
PID_FILE = os.path.join(RUNTIME_DIR, 'forge.pid')
WORKSPACE = os.getenv('WORKSPACE_DIR', '/workspace')

# Candidate uvicorn
UVICORN_CANDIDATES = ['/opt/forge-venv/bin/uvicorn']

def find_uvicorn():
    # Allow explicit override for testing or special setups
    env_exe = os.getenv('FORGE_UVICORN_EXEC')
    if env_exe:
        return env_exe
    # Prefer uvicorn from PATH (useful for test overrides), then fallback to known venv path
    exe = shutil.which('uvicorn')
    if exe:
        return exe
    for c in UVICORN_CANDIDATES:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None

child_proc = None

def _handle_signal(sig, frame):
    global child_proc
    if child_proc and child_proc.poll() is None:
        try:
            # send signal to the child's process group
            os.killpg(child_proc.pid, sig)
        except Exception:
            try:
                child_proc.terminate()
            except Exception:
                pass

def main():
    global child_proc
    uv = find_uvicorn()
    if not uv:
        print('No uvicorn binary found; exiting', file=sys.stderr)
        sys.exit(1)

    os.makedirs(RUNTIME_DIR, exist_ok=True)

    cmd = [uv, 'web.server:create_app', '--host', '0.0.0.0', '--port', '7860', '--log-level', 'info']
    # open logs in workspace if available
    out_path = os.path.join(WORKSPACE, 'forge.out.log')
    err_path = os.path.join(WORKSPACE, 'forge.err.log')
    out = open(out_path, 'a')
    err = open(err_path, 'a')

    # set signal handlers
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # start child in its own process group so we can signal the group
    try:
        child_proc = subprocess.Popen(cmd, stdout=out, stderr=err, cwd=os.path.abspath(os.path.dirname(__file__)), preexec_fn=os.setsid)
    except Exception as e:
        print(f'Failed to start uvicorn: {e}', file=sys.stderr)
        sys.exit(1)

    # write pidfile for systemd to monitor
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(child_proc.pid))
    except Exception as e:
        print(f'Failed to write pidfile {PID_FILE}: {e}', file=sys.stderr)

    # wait for child to exit
    try:
        rc = child_proc.wait()
    finally:
        # cleanup pidfile
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception:
            pass
    sys.exit(rc)

if __name__ == '__main__':
    main()
