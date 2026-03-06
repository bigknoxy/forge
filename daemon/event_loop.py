import asyncio
import os
from agents.ralph import Ralph
from agents.henry import Henry
from memory.database import db
from datetime import datetime

class ForgeEventLoop:
    def __init__(self):
        self.ralph = Ralph()
        self.henry = Henry(self.ralph)
        self.running = False
        self._bg_tasks = []

    async def start(self):
        self.running = True
        # Ensure workspace dirs
        os.makedirs(os.getenv('WORKSPACE_DIR','/workspace') + '/logs', exist_ok=True)
        # Start Henry loop and keep a reference so we can cancel on stop
        henry_task = asyncio.create_task(self._safe_run(self.henry.run_loop, 'henry'))
        self._bg_tasks.append(henry_task)

        # Dispatcher loop: poll for pending tasks and dispatch to assigned agent
        while self.running:
            try:
                # If application-level maintenance mode is set, skip dispatching and short-circuit
                if db.get_state('maintenance'):
                    db.write_log('event_loop', 'maintenance', 'Skipping dispatch due to maintenance flag')
                    await asyncio.sleep(1)
                    continue
                # Fetch next pending task
                pending = db.conn.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY created_at ASC LIMIT 1").fetchone()
                if pending:
                    assigned = (pending['assigned_to'] or '').lower()
                    task_id = pending['id']
                    db.write_log('event_loop', 'dispatch', f'Dispatching task {task_id} to {assigned}')
                    # Mark as in_progress to avoid duplicate dispatch
                    db.conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", ('in_progress', datetime.utcnow().isoformat(), task_id))
                    db.conn.commit()
                    # Dispatch to agent in background to keep loop responsive
                    t = asyncio.create_task(self._dispatch_to_agent(assigned, pending))
                    # attach to bg tasks and ensure we remove when done
                    self._bg_tasks.append(t)
                    def _done_cb(task):
                        try:
                            self._bg_tasks.remove(task)
                        except Exception:
                            pass
                    t.add_done_callback(_done_cb)
                # clear wake signal if set
                if db.get_state('wake_signal'):
                    db.set_state('wake_signal','')
            except Exception as e:
                db.write_log('event_loop', 'error', str(e), status='error')
            await asyncio.sleep(1)

    async def _safe_run(self, coro_func, name):
        # Run long-lived coro functions and ensure exceptions are logged but don't kill the loop
        try:
            await coro_func()
        except asyncio.CancelledError:
            db.write_log('event_loop', 'info', f'{name} cancelled')
            raise
        except Exception as e:
            db.write_log('event_loop', 'error', f'{name} crashed: {e}', status='error')

    async def _dispatch_to_agent(self, assigned: str, pending_row):
        task_id = pending_row['id']
        try:
            if assigned == 'henry':
                await self.henry.handle_task(pending_row)
            elif assigned == 'ralph':
                await self.ralph.receive_task(task_id, pending_row['description'])
            elif assigned == 'charlie':
                await self.ralph.charlie.receive_task(task_id, pending_row['description'])
            elif assigned == 'scout':
                await self.ralph.scout.receive_task(task_id, pending_row['description'])
            elif assigned == 'quinn':
                # Quinn review expects a path or description
                await self.ralph.quinn.review_task(pending_row['description'])
            else:
                # unknown - mark back pending
                db.conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", ('pending', datetime.utcnow().isoformat(), task_id))
                db.conn.commit()
        except Exception as e:
            db.write_log('event_loop', 'error', f'Error dispatching task {task_id} to {assigned}: {e}', status='error')
            # mark task failed
            try:
                db.conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", ('failed', datetime.utcnow().isoformat(), task_id))
                db.conn.commit()
            except Exception:
                pass

    def stop(self):
        self.running = False
        # cancel background tasks
        for t in list(self._bg_tasks):
            try:
                t.cancel()
            except Exception:
                pass
