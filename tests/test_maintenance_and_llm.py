import os
import subprocess
import sys
import sqlite3
import tempfile
import types
import importlib

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FORGE_CLI = os.path.join(REPO_DIR, 'forge_cli.py')
DB = os.getenv('DATA_DIR', '/data') + '/forge.db'
import sys
# Ensure repo root is on sys.path for imports
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)


def _run_cli(args, env=None):
    cmd = [sys.executable, FORGE_CLI] + args
    e = os.environ.copy()
    if env:
        e.update(env)
    res = subprocess.run(cmd, capture_output=True, text=True, env=e)
    return res


def test_pause_resume_app_and_llm_block():
    # Ensure maintenance flag is cleared
    res = _run_cli(['resume-app'])
    assert res.returncode == 0

    # Set maintenance
    res = _run_cli(['pause-app'])
    assert res.returncode == 0

    # Verify DB maintenance flag is set
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT value FROM checkpoints WHERE key='maintenance'")
    row = cur.fetchone()
    assert row is not None and row[0] == '1'
    conn.close()

    # Prepare a fake httpx module so tools.llm can import
    fake_httpx = types.ModuleType('httpx')

    class DummyResp:
        def __init__(self, status=200, data=None):
            self.status_code = status
            self._data = data or {}
        def json(self):
            return self._data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError('http error')

    class DummyAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, *a, **k):
            return DummyResp(200, {})

    class DummyClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def get(self, *a, **k):
            class R: 
                status_code=200
                def json(self):
                    return {}
            return R()
        def post(self, *a, **k):
            return DummyResp(200, {})

    fake_httpx.AsyncClient = DummyAsyncClient
    fake_httpx.Client = DummyClient

    sys.modules['httpx'] = fake_httpx

    # Import tools.llm after injecting fake httpx
    import importlib
    llm = importlib.import_module('tools.llm')
    importlib.reload(llm)

    # Ensure llm.db sees maintenance flag
    assert llm.db.get_state('maintenance') == '1', f"llm.db sees: {llm.db.get_state('maintenance')!r}"

    # call_model_sync should raise maintenance-mode RuntimeError
    try:
        try:
            llm.call_model_sync('henry', 'hello')
            assert False, 'call_model_sync did not raise under maintenance'
        except Exception as e:
            assert 'maintenance' in str(e).lower()
    finally:
        # cleanup: clear maintenance
        _run_cli(['resume-app'])
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT value FROM checkpoints WHERE key='maintenance'")
        row = cur.fetchone()
        conn.close()
        assert row is not None and row[0] == ''


def test_resume_allows_normal_errors():
    # make sure maintenance cleared
    _run_cli(['resume-app'])
    # inject fake httpx again and reload
    import types, sys, importlib
    fake_httpx = types.ModuleType('httpx')
    class DummyClient:
        def __init__(self,*a,**k):
            pass
        def __enter__(self):
            return self
        def __exit__(self,*a):
            return False
        def get(self,*a,**k):
            class R:
                status_code=200
                def json(self):
                    return {}
            return R()
    fake_httpx.Client = DummyClient
    class DummyAsyncClient:
        def __init__(self,*a,**k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self,*a):
            return False
        async def post(self,*a,**k):
            class R:
                status_code=200
                def json(self):
                    return {}
                def raise_for_status(self):
                    return None
            return R()
    fake_httpx.AsyncClient = DummyAsyncClient
    sys.modules['httpx'] = fake_httpx
    llm = importlib.import_module('tools.llm')
    importlib.reload(llm)

    # Now call_model_sync should raise a non-maintenance error (because no model configured)
    try:
        llm.call_model_sync('henry', 'hi')
        assert False, 'expected no-model-configured error'
    except Exception as e:
        assert 'no model configured' in str(e).lower()

