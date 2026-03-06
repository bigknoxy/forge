import os
import httpx
import json
import time
import asyncio
from typing import Optional, Dict, Any
from memory.database import db

OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_BASE = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
OLLAMA_BASE = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
OLLAMA_MAX_CONCURRENCY = int(os.getenv('OLLAMA_MAX_CONCURRENCY', '1'))
LLAMA_CPP_BASE = os.getenv('LLAMA_CPP_BASE_URL', None)
LLAMA_CPP_ENABLED = os.getenv('LLAMA_CPP_ENABLED', 'false').lower() in ('1','true','yes')
LLAMA_CPP_DEFAULT_MODEL = os.getenv('LLAMA_CPP_DEFAULT_MODEL')
LLAMA_CPP_MAX_CONCURRENCY = int(os.getenv('LLAMA_CPP_MAX_CONCURRENCY', '1'))

MODEL_MAP = {
    'henry': os.getenv('HENRY_MODEL'),
    'ralph': os.getenv('RALPH_MODEL'),
    'charlie': os.getenv('CHARLIE_MODEL'),
    'scout': os.getenv('SCOUT_MODEL'),
    'quinn': os.getenv('QUINN_MODEL'),
}

# Simple in-process circuit breaker state per-backend
_BREAKER: Dict[str, Dict[str, Any]] = {
    'ollama': {'failures': 0, 'backoff_until': 0},
    'openrouter': {'failures': 0, 'backoff_until': 0},
    'llamacpp': {'failures': 0, 'backoff_until': 0},
}

# asyncio-friendly semaphore and counters for llama.cpp
_LLAMA_CPP_SEM = asyncio.Semaphore(LLAMA_CPP_MAX_CONCURRENCY)
_LLAMA_CPP_INUSE = 0
_LLAMA_CPP_WAITERS = 0

# asyncio-friendly semaphore and counters for ollama
_OLLAMA_SEM = asyncio.Semaphore(OLLAMA_MAX_CONCURRENCY)
_OLLAMA_INUSE = 0
_OLLAMA_WAITERS = 0

_BACKOFF_BASE = 5  # seconds
_MAX_FAILURES_BEFORE_BACKOFF = 3


def _in_backoff(name: str) -> bool:
    s = _BREAKER.get(name, {})
    return s.get('backoff_until', 0) > time.time()


def _record_failure(name: str):
    s = _BREAKER.setdefault(name, {'failures': 0, 'backoff_until': 0})
    s['failures'] = s.get('failures', 0) + 1
    if s['failures'] >= _MAX_FAILURES_BEFORE_BACKOFF:
        # exponential backoff
        exp = s['failures'] - _MAX_FAILURES_BEFORE_BACKOFF
        backoff = _BACKOFF_BASE * (2 ** exp)
        s['backoff_until'] = time.time() + backoff


def _record_success(name: str):
    s = _BREAKER.setdefault(name, {'failures': 0, 'backoff_until': 0})
    s['failures'] = 0
    s['backoff_until'] = 0


async def _call_openrouter(model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500) -> str:
    if _in_backoff('openrouter'):
        raise RuntimeError('openrouter backend in backoff')
    if not OPENROUTER_KEY:
        raise RuntimeError('OPENROUTER_API_KEY not set')
    url = OPENROUTER_BASE.rstrip('/') + '/chat/completions'
    headers = {'Authorization': f'Bearer {OPENROUTER_KEY}', 'Content-Type': 'application/json'}
    messages = [
        {'role':'system','content': system},
        {'role':'user','content': user}
    ]
    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            _record_success('openrouter')
            if 'choices' in data and len(data['choices'])>0:
                text = data['choices'][0].get('message',{}).get('content') or data['choices'][0].get('text')
                return text
            return json.dumps(data)
    except Exception as e:
        _record_failure('openrouter')
        raise


async def _call_ollama(model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500) -> str:
    global _OLLAMA_INUSE, _OLLAMA_WAITERS
    if _in_backoff('ollama'):
        raise RuntimeError('ollama backend in backoff')
    candidate_paths = ['/api/chat', '/v1/chat/completions', '/chat']
    payloads = []
    messages = [
        {'role':'system','content': system},
        {'role':'user','content': user}
    ]
    payloads.append({'model': model, 'messages': messages, 'temperature': temperature})
    payloads.append({'model': model, 'input': user, 'temperature': temperature})

    last_exc = None
    # semaphore acquire with logging
    try:
        waited = False
        if _OLLAMA_SEM.locked():
            waited = True
            _OLLAMA_WAITERS += 1
            try:
                db.write_log('llm', 'llm_semaphore', f'waiting for ollama semaphore (max={OLLAMA_MAX_CONCURRENCY})')
            except Exception:
                pass
        await _OLLAMA_SEM.acquire()
        if waited:
            _OLLAMA_WAITERS = max(0, _OLLAMA_WAITERS - 1)
        _OLLAMA_INUSE += 1
        try:
            try:
                db.write_log('llm', 'llm_semaphore', f'acquired ollama semaphore (max={OLLAMA_MAX_CONCURRENCY})')
            except Exception:
                pass
            async with httpx.AsyncClient(timeout=20) as client:
                for path in candidate_paths:
                    url = OLLAMA_BASE.rstrip('/') + path
                    for payload in payloads:
                        try:
                            r = await client.post(url, json=payload)
                            if r.status_code == 404:
                                break
                            r.raise_for_status()
                            data = r.json()
                            _record_success('ollama')
                            if data is None:
                                return ''
                            if isinstance(data, dict) and 'choices' in data and len(data['choices'])>0:
                                return data['choices'][0].get('message',{}).get('content')
                            if isinstance(data, dict) and 'output' in data:
                                return data['output']
                            return json.dumps(data)
                        except Exception as e:
                            last_exc = e
                            continue
            # if we exit the loops without returning, treat as failure
            _record_failure('ollama')
            raise RuntimeError(f'ollama: all endpoints failed, last_error={last_exc}')
        finally:
            _OLLAMA_INUSE = max(0, _OLLAMA_INUSE - 1)
            try:
                _OLLAMA_SEM.release()
                db.write_log('llm', 'llm_semaphore', f'released ollama semaphore')
            except Exception:
                pass
    except Exception:
        _record_failure('ollama')
        raise RuntimeError(f'ollama: all endpoints failed, last_error={last_exc}')


async def _call_llamacpp(model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500) -> str:
    global _LLAMA_CPP_INUSE
    if not LLAMA_CPP_ENABLED or not LLAMA_CPP_BASE:
        raise RuntimeError('llamacpp not enabled or LLAMA_CPP_BASE not set')
    if _in_backoff('llamacpp'):
        raise RuntimeError('llamacpp backend in backoff')

    # Acquire asyncio semaphore
    waited = False
    try:
        if _LLAMA_CPP_SEM.locked():
            waited = True
            try:
                db.write_log('llm', 'llm_semaphore', f'waiting for llamacpp semaphore (max={LLAMA_CPP_MAX_CONCURRENCY})')
            except Exception:
                pass
        await _LLAMA_CPP_SEM.acquire()
        _LLAMA_CPP_INUSE += 1
        try:
            try:
                db.write_log('llm', 'llm_semaphore', f'acquired llamacpp semaphore (max={LLAMA_CPP_MAX_CONCURRENCY})')
            except Exception:
                pass
            candidate_paths = ['/api/chat', '/v1/chat/completions', '/chat/completions']
            messages = [
                {'role':'system','content': system},
                {'role':'user','content': user}
            ]
            payload = {'model': model, 'messages': messages, 'temperature': temperature}
            last_exc = None
            async with httpx.AsyncClient(timeout=60) as client:
                for path in candidate_paths:
                    url = LLAMA_CPP_BASE.rstrip('/') + path
                    try:
                        r = await client.post(url, json=payload)
                        if r.status_code == 404:
                            continue
                        r.raise_for_status()
                        data = r.json()
                        _record_success('llamacpp')
                        if isinstance(data, dict) and 'choices' in data and len(data['choices'])>0:
                            return data['choices'][0].get('message',{}).get('content') or data['choices'][0].get('text')
                        if isinstance(data, dict) and 'output' in data:
                            return data['output']
                        return json.dumps(data)
                    except Exception as e:
                        last_exc = e
                        continue
            _record_failure('llamacpp')
            raise RuntimeError(f'llamacpp: all endpoints failed, last_error={last_exc}')
        finally:
            _LLAMA_CPP_INUSE -= 1
            try:
                _LLAMA_CPP_SEM.release()
                db.write_log('llm', 'llm_semaphore', f'released llamacpp semaphore')
            except Exception:
                pass
    except Exception:
        # propagate
        raise


async def call_model(agent: str, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.2) -> str:
    """Call the configured model for the given agent. Returns text output.
    Falls back to RuntimeError if no model is configured for the agent.
    Adds per-call latency logging into the DB payload.
    """
    # Respect application-level maintenance flag: abort model calls when maintenance is active
    try:
        _maint = db.get_state('maintenance')
    except Exception:
        _maint = None
    if _maint:
        try:
            db.write_log(agent, 'llm_error', 'Call aborted due to maintenance mode', status='warning')
        except Exception:
            pass
        raise RuntimeError('maintenance mode: model calls are disabled')

    model = MODEL_MAP.get(agent)
    # If MODEL_MAP was populated at import time and envs changed later, allow env-based override
    if not model:
        env_name = f"{agent.upper()}_MODEL"
        model = os.getenv(env_name)
        if model:
            MODEL_MAP[agent] = model

    system = system_prompt or f"You are {agent}, an autonomous agent following the FORGE mission statement. Provide structured JSON when possible."
    if not model:
        raise RuntimeError(f'No model configured for agent {agent}')
    start = time.time()
    backend = 'unknown'
    mm = model
    try:
        if model.startswith('ollama/') or model.startswith('ollama'):
            backend = 'ollama'
            mm = model.split('/',1)[-1]
            text = await _call_ollama(mm, system, prompt, temperature)
        elif model.startswith('llamacpp/') or model.startswith('llamacpp'):
            backend = 'llamacpp'
            mm = model.split('/',1)[-1]
            text = await _call_llamacpp(mm, system, prompt, temperature)
        else:
            backend = 'openrouter'
            text = await _call_openrouter(model, system, prompt, temperature)
        duration = time.time() - start
        # write detailed per-call log to DB under the agent
        try:
            payload = {'backend': backend, 'model': mm, 'latency': duration, 'resp_len': len(text) if isinstance(text, str) else None}
            db.write_log(agent, 'llm_call', f'backend={backend} model={mm}', payload=payload)
        except Exception:
            pass
        return text
    except Exception as e:
        duration = time.time() - start
        try:
            payload = {'backend': backend, 'model': mm, 'latency': duration}
            db.write_log(agent, 'llm_error', str(e), status='warning', payload=payload)
        except Exception:
            pass
        raise


# synchronous wrapper for callers that are not async
def call_model_sync(agent: str, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.2) -> str:
    return asyncio.run(call_model(agent, prompt, system_prompt=system_prompt, temperature=temperature))


def try_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        # sometimes model returns code fences
        import re
        m = re.search(r"```(?:json)?\n([\s\S]+?)```", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
        return None


def get_backend_status() -> Dict[str, Any]:
    """Return diagnostic info about LLM backends (ollama, openrouter, llamacpp)
    """
    info = {}
    # Ollama
    try:
        if OLLAMA_BASE:
            with httpx.Client(timeout=5) as client:
                # try v1/models then /models
                r = client.get(OLLAMA_BASE.rstrip('/') + '/v1/models')
                if r.status_code == 200:
                    info['ollama'] = {'ok': True, 'models': r.json().get('data')}
                else:
                    r2 = client.get(OLLAMA_BASE.rstrip('/') + '/models')
                    info['ollama'] = {'ok': r2.status_code==200, 'status_code': r.status_code}
        else:
            info['ollama'] = {'ok': False, 'reason': 'OLLAMA_BASE not set'}
    except Exception as e:
        info['ollama'] = {'ok': False, 'error': str(e)}

    # OpenRouter
    try:
        if OPENROUTER_BASE:
            with httpx.Client(timeout=5) as client:
                r = client.get(OPENROUTER_BASE.rstrip('/'))
                info['openrouter'] = {'ok': r.status_code < 500}
        else:
            info['openrouter'] = {'ok': False, 'reason': 'OPENROUTER_BASE not set'}
    except Exception as e:
        info['openrouter'] = {'ok': False, 'error': str(e)}

    # Llama.cpp
    try:
        if LLAMA_CPP_BASE and LLAMA_CPP_ENABLED:
            with httpx.Client(timeout=5) as client:
                r = client.get(LLAMA_CPP_BASE.rstrip('/') + '/models')
                if r.status_code == 200:
                    # some servers return models under 'models' key
                    j = r.json()
                    models = j.get('models') or j.get('data') or j
                    info['llamacpp'] = {'ok': True, 'models': models}
                else:
                    info['llamacpp'] = {'ok': False, 'status_code': r.status_code}
        else:
            info['llamacpp'] = {'ok': False, 'reason': 'llamacpp disabled or LLAMA_CPP_BASE not set'}
    except Exception as e:
        info['llamacpp'] = {'ok': False, 'error': str(e)}

    # include breaker state and concurrency metrics
    info['circuit'] = {k: {'failures': v['failures'], 'backoff_until': v['backoff_until']} for k, v in _BREAKER.items()}
    info['llamacpp_concurrency'] = {'max': LLAMA_CPP_MAX_CONCURRENCY, 'in_use': _LLAMA_CPP_INUSE, 'waiters': _LLAMA_CPP_WAITERS}
    info['ollama_concurrency'] = {'max': OLLAMA_MAX_CONCURRENCY, 'in_use': _OLLAMA_INUSE, 'waiters': _OLLAMA_WAITERS}
    return info
