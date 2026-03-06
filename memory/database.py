import sqlite3
import os
from datetime import datetime
import json

DB_PATH = os.getenv('DATA_DIR', '/data') + '/forge.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    description TEXT,
    assigned_to TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT,
    recipient TEXT,
    content TEXT,
    metadata TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    path TEXT,
    manifest JSON,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    agent TEXT,
    action_type TEXT,
    description TEXT,
    status TEXT,
    payload JSON
);
CREATE TABLE IF NOT EXISTS checkpoints (
    key TEXT PRIMARY KEY,
    value TEXT
);
'''

class Database:
    def __init__(self, db_path: str = None):
        self.path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # set a timeout to avoid 'database is locked' in concurrent access from CLI
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        # set busy timeout pragma
        try:
            self.conn.execute('PRAGMA busy_timeout = 30000')
        except Exception:
            pass
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA)
        self.conn.commit()
        # ensure wake_signal exists
        cur.execute("INSERT OR IGNORE INTO checkpoints (key, value) VALUES ('wake_signal','')")
        self.conn.commit()

    def add_project(self, name, description=''):
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        cur.execute('INSERT OR IGNORE INTO projects (name,description,status,created_at,updated_at) VALUES (?,?,?,?,?)', (name,description,'open',now,now))
        self.conn.commit()

    def add_task(self, description, assigned_to=None, project_id=None):
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        cur.execute('INSERT INTO tasks (project_id,description,assigned_to,status,created_at,updated_at) VALUES (?,?,?,?,?,?)', (project_id,description,assigned_to or 'henry','pending',now,now))
        self.conn.commit()
        return cur.lastrowid

    def log_message(self, sender, recipient, content, metadata=None):
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        cur.execute('INSERT INTO messages (sender,recipient,content,metadata,created_at) VALUES (?,?,?,?,?)', (sender,recipient,content,json.dumps(metadata or {}),now))
        self.conn.commit()

    def register_skill(self, name, description, path, manifest):
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        cur.execute('INSERT OR REPLACE INTO skills (name,description,path,manifest,created_at) VALUES (?,?,?,?,?)', (name,description,path,json.dumps(manifest),now))
        self.conn.commit()

    def query_skills(self, query_text):
        cur = self.conn.cursor()
        cur.execute('SELECT * FROM skills WHERE name LIKE ? OR description LIKE ? LIMIT 20', (f'%{query_text}%',f'%{query_text}%'))
        return cur.fetchall()

    def write_log(self, agent, action_type, description, status='info', payload=None):
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        cur.execute('INSERT INTO logs (timestamp,agent,action_type,description,status,payload) VALUES (?,?,?,?,?,?)', (now,agent,action_type,description,status,json.dumps(payload or {})))
        self.conn.commit()
        # Also append to per-agent log file under /workspace/logs/<agent>.log as JSON lines
        try:
            ws = os.getenv('WORKSPACE_DIR', '/workspace')
            log_dir = os.path.join(ws, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            entry = {
                'timestamp': now,
                'agent': agent,
                'action_type': action_type,
                'description': description,
                'status': status,
                'payload': payload or {}
            }
            with open(os.path.join(log_dir, f"{agent}.log"), 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def get_state(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM checkpoints WHERE key = ?', (key,))
        row = cur.fetchone()
        return row['value'] if row else None

    def set_state(self, key, value):
        cur = self.conn.cursor()
        cur.execute('INSERT OR REPLACE INTO checkpoints (key, value) VALUES (?,?)', (key, value))
        self.conn.commit()

# module-level default
db = Database()