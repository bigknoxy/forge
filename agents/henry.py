import asyncio
from memory.database import db
from memory.checkpointer import checkpointer
from datetime import datetime
import json
from tools.file_tools import write_file
from tools.llm import call_model, try_parse_json
from tools.prompts import HENRY_PROMPT

AGENT_NAME = 'henry'

class Henry:
    def __init__(self, ralph):
        self.ralph = ralph
        self.agent = AGENT_NAME

    async def wake(self):
        # Respect application-level maintenance flag
        if db.get_state('maintenance'):
            db.write_log(self.agent, 'maintenance', 'Skipping wake due to maintenance flag')
            return
        # Check for pending tasks
        pending = db.conn.execute("SELECT * FROM tasks WHERE assigned_to='henry' AND status='pending' ORDER BY created_at ASC LIMIT 1").fetchone()
        if pending:
            await self.handle_task(pending)
            return
        # Idle behavior: propose 3 things and pick one
        proposals = await self.idle_proposals()
        choice = proposals[0]
        db.write_log(self.agent, 'idle_choice', f'Chose: {choice}')
        # Create a task and delegate to Ralph
        task_id = db.add_task(choice, assigned_to='ralph')
        db.write_log(self.agent, 'delegate', f'Delegated idle task to Ralph: {choice}', payload={'task_id': task_id})

    async def idle_proposals(self):
        # Use the model to propose ideas if available
        try:
            prompt = "List 3 concrete high-impact tasks FORGE can do right now toward its mission. Output as a JSON array of strings."
            out = await call_model(self.agent, prompt, system_prompt=HENRY_PROMPT)
            parsed = try_parse_json(out)
            if parsed and isinstance(parsed, list):
                return parsed
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # fallback
        projects = db.conn.execute('SELECT count(*) as cnt FROM projects').fetchone()['cnt']
        if projects == 0:
            return ['Create a new portfolio project and scaffold it','Audit current skills library for reuse','Research trending demo projects']
        return ['Improve README of open projects','Run QA on recent commits','Build a new small tool for the skill library']

    async def handle_task(self, task_row):
        desc = task_row['description']
        db.write_log(self.agent, 'received_task', desc)
        # Try to decompose into projects + tasks using the model
        try:
            prompt = ("You are Henry, the orchestrator. Given this user goal or task: \n\n" + desc +
                      "\n\nReturn JSON with keys: project_name (string), project_desc (string), tasks (array of {description, priority}).")
            out = await call_model(self.agent, prompt, system_prompt=HENRY_PROMPT)
            parsed = try_parse_json(out)
            if parsed and 'project_name' in parsed:
                pname = parsed.get('project_name')
                pdesc = parsed.get('project_desc','')
                db.add_project(pname, pdesc)
                # create tasks and send first to Ralph
                for t in parsed.get('tasks',[]):
                    tid = db.add_task(t.get('description'), assigned_to='ralph')
                    db.write_log(self.agent, 'delegate', f'Created task for Ralph: {t.get("description")}', payload={'task_id': tid})
                    # notify Ralph
                    await self.ralph.receive_task(tid, t.get('description'))
                # mark Henry's task done
                db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), task_row['id']))
                db.conn.commit()
                return
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # Henry delegates all engineering to Ralph (fallback)
        db.conn.execute('UPDATE tasks SET assigned_to=?, updated_at=? WHERE id=?', ('ralph', datetime.utcnow().isoformat(), task_row['id']))
        db.conn.commit()
        db.write_log(self.agent, 'delegate', f'Sent to Ralph: {desc}', payload={'task_id': task_row['id']})
        # also notify Ralph
        await self.ralph.receive_task(task_row['id'], desc)

    async def ask_user(self, question: str):
        # Henry can ask user one clarifying question by writing to messages
        db.log_message(self.agent, 'user', question)

    async def run_loop(self):
        # Continuous loop; called by event loop
        while True:
            try:
                await self.wake()
            except Exception as e:
                db.write_log(self.agent, 'error', str(e), status='error')
            await asyncio.sleep(int(__import__('os').getenv('WAKE_INTERVAL_SECONDS','300')))
