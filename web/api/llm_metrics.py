from fastapi import APIRouter
from memory.database import db
import json
from datetime import datetime, timedelta

router = APIRouter()

@router.get('/llm_metrics')
def llm_metrics():
    # Return recent LLM-related logs (llm_call, llm_error, llm_semaphore)
    cur = db.conn.cursor()
    rows = cur.execute("SELECT id,timestamp,agent,action_type,description,status,payload FROM logs WHERE action_type IN ('llm_call','llm_error','llm_semaphore') ORDER BY timestamp DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r['payload']) if r['payload'] else {}
        except Exception:
            payload = {}
        out.append({
            'id': r['id'],
            'timestamp': r['timestamp'],
            'agent': r['agent'],
            'action_type': r['action_type'],
            'description': r['description'],
            'status': r['status'],
            'payload': payload
        })
    return {'metrics': out}


@router.get('/llm_metrics/aggregate')
def llm_metrics_aggregate(window_minutes: int = 5, bucket_ms: int = 200, limit: int = 10000):
    """Return a latency histogram for recent llm_call events.
    window_minutes: how many minutes back to consider
    bucket_ms: bucket width in milliseconds
    limit: max rows to scan (protects DB)
    """
    cur = db.conn.cursor()
    min_ts = (datetime.utcnow() - timedelta(minutes=window_minutes)).isoformat()
    rows = cur.execute("SELECT id,timestamp,payload FROM logs WHERE action_type = 'llm_call' AND timestamp >= ? ORDER BY id DESC LIMIT ?", (min_ts, limit)).fetchall()
    buckets = {}
    max_latency = 0
    count = 0
    for r in rows:
        try:
            payload = json.loads(r['payload']) if r['payload'] else {}
            latency = float(payload.get('latency') or 0)
        except Exception:
            latency = 0
        if latency is None:
            continue
        count += 1
        max_latency = max(max_latency, latency)
        b = int(latency * 1000.0 // bucket_ms) * bucket_ms
        buckets[b] = buckets.get(b, 0) + 1
    # produce sorted buckets
    if count == 0:
        return {'count': 0, 'buckets': [], 'bucket_ms': bucket_ms}
    bucket_items = sorted([{'bucket_start_ms': k, 'count': v} for k, v in buckets.items()], key=lambda x: x['bucket_start_ms'])
    return {'count': count, 'buckets': bucket_items, 'bucket_ms': bucket_ms, 'window_minutes': window_minutes}
