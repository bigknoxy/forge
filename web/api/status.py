from fastapi import APIRouter
from memory.database import db
from tools.file_tools import list_directory

router = APIRouter()

@router.get('/status')
def get_status():
    projects = [dict(p) for p in db.conn.execute('SELECT * FROM projects').fetchall()]
    tasks = [dict(t) for t in db.conn.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50').fetchall()]
    logs = []
    return {'projects': projects, 'tasks': tasks, 'logs': logs}
