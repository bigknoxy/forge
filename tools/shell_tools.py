import subprocess
import shlex
import os
from pathlib import Path
from typing import Tuple, Dict, Optional

WORKSPACE = os.getenv('WORKSPACE_DIR', '/workspace')


def run_shell(command: str, timeout: int = 30, env: Optional[Dict[str,str]] = None) -> Tuple[int, str, str]:
    """Execute a shell command within the workspace. Returns (exitcode, stdout, stderr)
    env: optional dict of environment variables to set (merged with current env)
    """
    # Prepare environment
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    # Ensure the command is run in the workspace
    proc = subprocess.Popen(command, shell=True, cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=proc_env)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return proc.returncode or 1, out, err
    return proc.returncode, out, err


def git_operation(command: str) -> Tuple[int, str, str]:
    # Simple wrapper assuming gh and git are configured
    return run_shell(command)
