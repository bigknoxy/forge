from fastapi import APIRouter
from memory.database import db
from datetime import datetime
import os

from tools.llm import get_backend_status

router = APIRouter()

@router.get('/health')
def health():
    # Basic health info: uptime and agent statuses and LLM connectivity
    try:
        # last wake signal
        wake = db.get_state('wake_signal')
        projects = db.conn.execute('SELECT COUNT(*) as cnt FROM projects').fetchone()['cnt']
        pending = db.conn.execute("SELECT COUNT(*) as cnt FROM tasks WHERE status IN ('pending','in_review','in_progress')").fetchone()['cnt']

        # LLM backend diagnostics
        backend_info = get_backend_status()

        return {
            'status':'ok',
            'time': datetime.utcnow().isoformat(),
            'projects': projects,
            'pending_tasks': pending,
            'wake_signal': wake,
            'workspace': os.getenv('WORKSPACE_DIR','/workspace'),
            'llm_backends': backend_info
        }
    except Exception as e:
        return {'status':'error','error': str(e)}
