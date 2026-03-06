from fastapi import APIRouter, HTTPException
from typing import List
from memory.database import db

router = APIRouter()

# IDF cache (global)
IDF_CACHE = {
    'N': 0,
    'df_uni': {},
    'df_bi': {},
    'updated_at': None
}

@router.get('/projects')
def list_projects():
    """Return projects with basic aggregates (task counts, open/in-progress counts, latest_update).
    Uses the tasks table to compute counts.
    Also include an 'Unassigned' pseudo-project for tasks with no project_id.
    """
    cur = db.conn.cursor()
    cur.execute('SELECT id, name, description, created_at, updated_at FROM projects ORDER BY updated_at DESC')
    prows = cur.fetchall()
    out = []
    for p in prows:
        pid = p['id']
        # total tasks
        t_total = cur.execute('SELECT COUNT(1) as c FROM tasks WHERE project_id=?', (pid,)).fetchone()['c']
        t_pending = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id=? AND status='pending'", (pid,)).fetchone()['c']
        t_inprog = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id=? AND status='in_progress'", (pid,)).fetchone()['c']
        t_done = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id=? AND status='done'", (pid,)).fetchone()['c']
        # latest task update time
        latest = cur.execute('SELECT MAX(updated_at) as m FROM tasks WHERE project_id=?', (pid,)).fetchone()['m']
        # compute progress and derived status
        progress_pct = int((t_done / t_total) * 100) if t_total and t_total > 0 else 0
        if t_total > 0 and t_done == t_total:
            derived_status = 'done'
        elif t_inprog > 0:
            derived_status = 'in_progress'
        elif t_pending > 0:
            derived_status = 'pending'
        elif t_total == 0:
            derived_status = 'idle'
        else:
            derived_status = 'unknown'

        out.append({
            'id': pid,
            'name': p['name'],
            'description': p['description'],
            'total_tasks': t_total,
            'pending': t_pending,
            'in_progress': t_inprog,
            'done': t_done,
            'latest_task_update': latest or p['updated_at'],
            'progress_pct': progress_pct,
            'status': derived_status
        })
    # include unassigned tasks summary
    un_total = cur.execute('SELECT COUNT(1) as c FROM tasks WHERE project_id IS NULL').fetchone()['c']
    un_pending = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id IS NULL AND status='pending'").fetchone()['c']
    un_inprog = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id IS NULL AND status='in_progress'").fetchone()['c']
    un_done = cur.execute("SELECT COUNT(1) as c FROM tasks WHERE project_id IS NULL AND status='done'").fetchone()['c']
    if un_total > 0:
        out.insert(0, {
            'id': 0,
            'name': 'Unassigned',
            'description': 'Tasks not assigned to any project',
            'total_tasks': un_total,
            'pending': un_pending,
            'in_progress': un_inprog,
            'done': un_done,
            'latest_task_update': None
        })
    return {'projects': out}

@router.get('/projects/idf/status')
def idf_status():
    """Return current IDF cache status for monitoring."""
    global IDF_CACHE
    N = IDF_CACHE.get('N', 0)
    dfu = IDF_CACHE.get('df_uni', {}) or {}
    dfb = IDF_CACHE.get('df_bi', {}) or {}
    updated = IDF_CACHE.get('updated_at')
    return {'N': N, 'unique_unigrams': len(dfu), 'unique_bigrams': len(dfb), 'updated_at': updated}

@router.get('/projects/{project_id}')
def get_project(project_id: str):
    cur = db.conn.cursor()
    # support a pseudo-project id of '0' or 'unassigned' to return tasks with NULL project_id
    if project_id in ('0', 'unassigned'):
        cur.execute('SELECT id, project_id, description, assigned_to, status, created_at, updated_at FROM tasks WHERE project_id IS NULL ORDER BY updated_at DESC LIMIT 200')
        tasks = [dict(row) for row in cur.fetchall()]
        return {'project': {'id': 0, 'name': 'Unassigned', 'description': 'Tasks not assigned to any project'}, 'tasks': tasks}

    try:
        pid = int(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid project id')

    cur.execute('SELECT id, name, description, created_at, updated_at FROM projects WHERE id = ?', (pid,))
    p = cur.fetchone()
    if not p:
        raise HTTPException(status_code=404, detail='Project not found')
    cur.execute('SELECT id, project_id, description, assigned_to, status, created_at, updated_at FROM tasks WHERE project_id = ? ORDER BY updated_at DESC LIMIT 200', (pid,))
    tasks = [dict(row) for row in cur.fetchall()]
    return {'project': dict(p), 'tasks': tasks}


@router.get('/projects/{project_id}/suggest_matches')
def suggest_matches(project_id: str, min_score: float = 0.12, limit: int = 200):
    """Return candidate unassigned tasks that likely belong to the given project using improved heuristic matching.
    The endpoint computes a simple token-overlap score (unigrams + bigrams) and returns candidates
    with score >= min_score (0..1). Results are sorted by score desc.
    """
    import re
    cur = db.conn.cursor()
    # fetch project metadata to derive keywords
    if project_id in ('0', 'unassigned'):
        raise HTTPException(status_code=400, detail='Cannot suggest matches for Unassigned')
    try:
        pid = int(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid project id')
    cur.execute('SELECT id, name, description FROM projects WHERE id = ?', (pid,))
    p = cur.fetchone()
    if not p:
        raise HTTPException(status_code=404, detail='Project not found')
    text = ((p['name'] or '') + ' ' + (p['description'] or '')).lower()

    # tokenize using regex (extract alphanumeric runs), remove short tokens and common stopwords
    stopwords = set(['project','task','tasks','work','create','build','implement','using','use','design','develop'])
    tokens_all = re.findall(r'[a-z0-9]{3,}', text)
    tokens = [t for t in tokens_all if t not in stopwords]

    def ngrams(words, n):
        return [' '.join(words[i:i+n]) for i in range(len(words)-n+1)]

    proj_unigrams_list = list(dict.fromkeys(tokens))  # preserve order, unique
    proj_unigrams = set(proj_unigrams_list)
    proj_bigrams = set(ngrams(proj_unigrams_list, 2))

    # pick top keyword tokens to use for initial SQL filter (prefer longer tokens)
    key_tokens = sorted([t for t in proj_unigrams if len(t) >= 4], key=lambda x: -len(x))[:6]
    if not key_tokens:
        key_tokens = ['mobile','game','browser','pwa','playable']

    # fetch recent unassigned tasks but pre-filter using SQL LIKE on key_tokens to reduce noise
    conds = ' OR '.join(['description LIKE ?' for _ in key_tokens])
    params = ['%'+k+'%' for k in key_tokens]
    sql = f"SELECT id,description,assigned_to,status,project_id,updated_at FROM tasks WHERE project_id IS NULL AND ({conds}) ORDER BY updated_at DESC LIMIT ?"
    rows = cur.execute(sql, params + [limit*2]).fetchall()

    # Try to use cached global IDF values; if missing/empty, compute a fresh global IDF sample
    import math, datetime
    global IDF_CACHE
    N = IDF_CACHE.get('N', 0)
    df_uni = IDF_CACHE.get('df_uni', {}) or {}
    df_bi = IDF_CACHE.get('df_bi', {}) or {}

    if N <= 0:
        # compute global IDF cache synchronously
        def refresh_global_idf(sample_limit=2000):
            cur2 = db.conn.cursor()
            rows_all = cur2.execute('SELECT description FROM tasks WHERE description IS NOT NULL ORDER BY updated_at DESC LIMIT ?', (sample_limit,)).fetchall()
            dfu = {}
            dfb = {}
            NN = 0
            for rr in rows_all:
                NN += 1
                desc = (rr['description'] or '').lower()
                desc_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in desc.split()]
                desc_tokens = [t for t in desc_tokens if t and len(t) >= 3]
                uniq = set(desc_tokens)
                for u in uniq:
                    dfu[u] = dfu.get(u, 0) + 1
                bis = set(ngrams(list(uniq), 2))
                for b in bis:
                    dfb[b] = dfb.get(b, 0) + 1
            IDF_CACHE['N'] = NN
            IDF_CACHE['df_uni'] = dfu
            IDF_CACHE['df_bi'] = dfb
            IDF_CACHE['updated_at'] = datetime.datetime.utcnow().isoformat()
            return NN, dfu, dfb
        try:
            refresh_global_idf()
            N = IDF_CACHE['N']
            df_uni = IDF_CACHE['df_uni']
            df_bi = IDF_CACHE['df_bi']
        except Exception:
            # fallback to local counts from rows
            N = 0
            df_uni = {}
            df_bi = {}

    def idf(df_count):
        return math.log((N + 1) / (df_count + 1)) + 1.0 if N > 0 else 1.0

    proj_uni_idf = {u: idf(df_uni.get(u, 0)) for u in proj_unigrams}
    proj_bi_idf = {b: idf(df_bi.get(b, 0)) for b in proj_bigrams}

    # scoring: TF-IDF style overlap score
    candidates = []
    for r in rows:
        desc = (r['description'] or '').lower()
        desc_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in desc.split()]
        desc_tokens = [t for t in desc_tokens if t and len(t) >= 3]
        desc_unigrams = set(desc_tokens)
        desc_bigrams = set(ngrams(desc_tokens, 2))

        uni_score = 0.0
        for u in proj_unigrams:
            if u in desc_unigrams:
                uni_score += proj_uni_idf.get(u, 1.0)
        bi_score = 0.0
        for b in proj_bigrams:
            if b in desc_bigrams:
                bi_score += proj_bi_idf.get(b, 1.0)

        denom = sum(proj_uni_idf.values()) + 2.0 * sum(proj_bi_idf.values())
        if denom <= 0:
            score = 0.0
        else:
            score = (uni_score + 2.0 * bi_score) / denom

        try:
            ass = (r['assigned_to'] or '').lower()
            if ass and ass in text:
                score += 0.06
        except Exception:
            pass

        if score >= min_score:
            rowd = dict(r)
            rowd['score'] = round(score, 4)
            candidates.append(rowd)

    candidates.sort(key=lambda x: (-x['score'], x.get('updated_at') or ''))
    return {'candidates': candidates[:limit]}


# async IDF refresher utility
async def async_idf_refresher(interval_sec: int = 600):
    import asyncio, re, math, datetime
    loop = asyncio.get_running_loop()
    while True:
        try:
            # compute in threadpool to avoid blocking the event loop
            await loop.run_in_executor(None, recompute_idf_internal)
        except Exception:
            pass
        await asyncio.sleep(interval_sec)


def recompute_idf_internal(sample_limit: int = 5000):
    import re, datetime
    cur = db.conn.cursor()
    rows_all = cur.execute('SELECT description FROM tasks WHERE description IS NOT NULL ORDER BY updated_at DESC LIMIT ?', (sample_limit,)).fetchall()
    dfu = {}
    dfb = {}
    N = 0
    def ngrams(words, n):
        return [' '.join(words[i:i+n]) for i in range(len(words)-n+1)]
    for rr in rows_all:
        N += 1
        desc = (rr['description'] or '').lower()
        desc_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in desc.split()]
        desc_tokens = [t for t in desc_tokens if t and len(t) >= 3]
        uniq = set(desc_tokens)
        for u in uniq:
            dfu[u] = dfu.get(u, 0) + 1
        bis = set(ngrams(list(uniq), 2))
        for b in bis:
            dfb[b] = dfb.get(b, 0) + 1
    IDF_CACHE['N'] = N
    IDF_CACHE['df_uni'] = dfu
    IDF_CACHE['df_bi'] = dfb
    IDF_CACHE['updated_at'] = datetime.datetime.utcnow().isoformat()
    return {'N': N, 'unique_unigrams': len(dfu), 'unique_bigrams': len(dfb), 'updated_at': IDF_CACHE['updated_at']}


@router.post('/projects/recompute_idf')
async def recompute_idf():
    """Schedule a background global IDF recompute and return an immediate acknowledgement with current IDF snapshot.
    The actual heavy recompute is performed by recompute_idf_internal in a threadpool so this endpoint returns quickly.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    # schedule recompute in the threadpool (do not await)
    try:
        loop.run_in_executor(None, recompute_idf_internal)
    except Exception:
        pass
    # return current snapshot so callers have immediate visibility
    global IDF_CACHE
    N = IDF_CACHE.get('N', 0)
    dfu = IDF_CACHE.get('df_uni', {}) or {}
    dfb = IDF_CACHE.get('df_bi', {}) or {}
    updated = IDF_CACHE.get('updated_at')
    return {'started': True, 'N': N, 'unique_unigrams': len(dfu), 'unique_bigrams': len(dfb), 'updated_at': updated, 'note': 'recompute scheduled in background'}


@router.post('/projects/{project_id}/assign_tasks')
def assign_tasks(project_id: str, payload: dict):
    """Assign a list of task ids to the project. Body: {"task_ids": [1,2,3]}"""
    cur = db.conn.cursor()
    try:
        pid = int(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid project id')
    task_ids = payload.get('task_ids') if isinstance(payload, dict) else None
    if not task_ids or not isinstance(task_ids, list):
        raise HTTPException(status_code=400, detail='task_ids must be provided as a list')
    # validate tasks exist
    q = 'SELECT id FROM tasks WHERE id IN ({})'.format(','.join('?' for _ in task_ids))
    found = [r['id'] for r in cur.execute(q, task_ids).fetchall()]
    missing = [tid for tid in task_ids if tid not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f'Tasks not found: {missing}')
    # perform update
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    upd_q = 'UPDATE tasks SET project_id = ?, updated_at = ? WHERE id = ?'
    for tid in task_ids:
        cur.execute(upd_q, (pid, now, tid))
    db.conn.commit()
    return {'assigned_count': len(task_ids), 'task_ids': task_ids}
