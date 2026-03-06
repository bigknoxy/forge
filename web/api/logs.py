from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import os
import asyncio
import json
from memory.database import db

router = APIRouter()
LOG_DIR = os.getenv('WORKSPACE_DIR', '/workspace') + '/logs'

@router.get('/logs')
def list_logs():
    if not os.path.isdir(LOG_DIR):
        return {'logs': []}
    files = [f for f in os.listdir(LOG_DIR) if f.endswith('.log')]
    return {'logs': files}

# helper to convert DB row to dict

def _row_to_dict(r):
    try:
        payload = json.loads(r['payload']) if r['payload'] else {}
    except Exception:
        payload = {}
    return {
        'id': r['id'],
        'timestamp': r['timestamp'],
        'agent': r['agent'],
        'action_type': r['action_type'],
        'description': r['description'],
        'status': r['status'],
        'payload': payload
    }


# Try to use watchdog for efficient file watching; fall back to polling
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except Exception:
    HAS_WATCHDOG = False

@router.get('/logs/stream')
def stream_logs():
    """Deprecated alias for stream_all_logs. Use /logs/all/stream."""
    return stream_all_logs()


@router.get('/logs/all/stream')
def stream_all_logs():
    """SSE streaming of all agent logs (new lines only).
    Uses watchdog if available for low-latency updates, otherwise falls back to polling."""
    if not os.path.isdir(LOG_DIR):
        async def _poll_stream_init():
            while not os.path.isdir(LOG_DIR):
                await asyncio.sleep(1)
            return
        return StreamingResponse(_poll_stream(), media_type='text/event-stream')

    if HAS_WATCHDOG:
        # Use an asyncio.Queue to bridge watchdog events to the ASGI response
        queue = asyncio.Queue()

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                if not event.src_path.endswith('.log'):
                    return
                # read new lines and put into queue
                try:
                    with open(event.src_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    # send last 100 lines for safety
                    for ln in lines[-100:]:
                        queue.put_nowait((os.path.basename(event.src_path), ln.strip()))
                except Exception:
                    pass

        observer = Observer()
        handler = _Handler()
        observer.schedule(handler, LOG_DIR, recursive=False)
        observer.start()

        async def event_generator():
            try:
                # yield existing tail first
                for fname in sorted(os.listdir(LOG_DIR)):
                    if not fname.endswith('.log'):
                        continue
                    path = os.path.join(LOG_DIR, fname)
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        for ln in lines[-100:]:
                            yield f"data: [{fname}] {ln.strip()}\n\n"
                    except Exception:
                        continue
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    fname, ln = item
                    yield f"data: [{fname}] {ln}\n\n"
            finally:
                try:
                    observer.stop()
                    observer.join(timeout=1)
                except Exception:
                    pass

        return StreamingResponse(event_generator(), media_type='text/event-stream')
    else:
        # fallback to polling implementation
        async def _poll_stream():
            files_mtime = {}
            while True:
                if not os.path.isdir(LOG_DIR):
                    await asyncio.sleep(1)
                    continue
                for fname in sorted(os.listdir(LOG_DIR)):
                    if not fname.endswith('.log'):
                        continue
                    path = os.path.join(LOG_DIR, fname)
                    try:
                        mtime = os.path.getmtime(path)
                    except Exception:
                        continue
                    last_m = files_mtime.get(path, 0)
                    if mtime <= last_m:
                        continue
                    # read new lines
                    with open(path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    # send only the tail since last_m
                    for ln in lines[-100:]:
                        yield f"data: [{fname}] {ln.strip()}\n\n"
                    files_mtime[path] = mtime
                await asyncio.sleep(1)
        return StreamingResponse(_poll_stream(), media_type='text/event-stream')


@router.get('/logs/{agent}/stream')
def stream_agent_logs(agent: str, lines: int = 200):
    """SSE stream for a single agent log file."""
    path = os.path.join(LOG_DIR, f"{agent}.log")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='Agent log not found')

    async def _stream_file():
        last_m = 0
        while True:
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                await asyncio.sleep(1)
                continue
            if mtime > last_m:
                with open(path, 'r', encoding='utf-8') as f:
                    lines_all = f.readlines()
                for ln in lines_all[-lines:]:
                    yield f"data: [{agent}.log] {ln.strip()}\n\n"
                last_m = mtime
            await asyncio.sleep(1)

    return StreamingResponse(_stream_file(), media_type='text/event-stream')

@router.get('/logs/page')
def page_all_logs(before_id: int = None, after_id: int = None, limit: int = 50):
    """Cursor-based paging for all agent logs. Supports before_id and after_id. Returns rows in descending id order."""
    try:
        cur = db.conn.cursor()
        if after_id:
            rows = cur.execute(
                "SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, limit)
            ).fetchall()
            rows = list(reversed(rows))
        elif before_id:
            rows = cur.execute(
                "SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id, limit)
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT id,timestamp,agent,action_type,description,status,payload FROM logs ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

        out = []
        min_id = None
        max_id = None
        for r in rows:
            rowd = _row_to_dict(r)
            out.append(rowd)
            if min_id is None or r['id'] < min_id:
                min_id = r['id']
            if max_id is None or r['id'] > max_id:
                max_id = r['id']

        has_prev = False
        has_next = False
        if max_id is not None:
            x = cur.execute("SELECT 1 FROM logs WHERE id > ? LIMIT 1", (max_id,)).fetchone()
            has_prev = bool(x)
        if min_id is not None:
            y = cur.execute("SELECT 1 FROM logs WHERE id < ? LIMIT 1", (min_id,)).fetchone()
            has_next = bool(y)

        return {
            'rows': out,
            'has_prev': has_prev,
            'has_next': has_next,
            'earliest_id': min_id,
            'latest_id': max_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/logs/{agent}')
def get_log(agent: str, lines: int = 200):
    # simple tail of an agent log
    path = os.path.join(LOG_DIR, f"{agent}.log")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='Agent log not found')
    with open(path, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
    tail = [l.strip() for l in all_lines[-lines:]]
    return {'agent': agent, 'lines': tail}

@router.get('/logs/{agent}/page')
def page_logs(agent: str, before_id: int = None, after_id: int = None, limit: int = 50):
    """Cursor-based paging for agent logs (from DB). Supports before_id (older) and after_id (newer).
    Returns rows in descending id order (newest first) and navigation flags.
    """
    try:
        cur = db.conn.cursor()
        if after_id:
            # fetch rows newer than after_id (ascending), then return in descending order
            rows = cur.execute("SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE agent=? AND id > ? ORDER BY id ASC LIMIT ?", (agent, after_id, limit)).fetchall()
            rows = list(reversed(rows))
        elif before_id:
            rows = cur.execute("SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE agent=? AND id < ? ORDER BY id DESC LIMIT ?", (agent, before_id, limit)).fetchall()
        else:
            rows = cur.execute("SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit)).fetchall()
        out = []
        min_id = None
        max_id = None
        for r in rows:
            rowd = _row_to_dict(r)
            out.append(rowd)
            if min_id is None or r['id'] < min_id:
                min_id = r['id']
            if max_id is None or r['id'] > max_id:
                max_id = r['id']
        # navigation flags
        has_prev = False
        has_next = False
        if max_id is not None:
            x = cur.execute("SELECT 1 FROM logs WHERE agent=? AND id > ? LIMIT 1", (agent, max_id)).fetchone()
            has_prev = bool(x)
        if min_id is not None:
            y = cur.execute("SELECT 1 FROM logs WHERE agent=? AND id < ? LIMIT 1", (agent, min_id)).fetchone()
            has_next = bool(y)
        return {'rows': out, 'has_prev': has_prev, 'has_next': has_next, 'earliest_id': min_id, 'latest_id': max_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
