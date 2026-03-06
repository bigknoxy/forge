import asyncio
from memory.database import db
from datetime import datetime
from agents.charlie import Charlie
from agents.scout import Scout
from agents.quinn import Quinn
from tools.llm import call_model, try_parse_json
from tools.prompts import RALPH_PROMPT

AGENT_NAME = 'ralph'

class Ralph:
    def __init__(self):
        self.agent = AGENT_NAME
        self.charlie = Charlie()
        self.scout = Scout()
        self.quinn = Quinn()

    async def receive_task(self, task_id: int, description: str):
        db.write_log(self.agent, 'receive_task', description, payload={'task_id': task_id})
        # Decompose task into simple subtasks (try using LLM)
        subtasks = await self.decompose(description)
        # Assign to Charlie and Scout depending on type
        for s in subtasks:
            assigned = s.get('assigned')
            desc = s.get('desc')
            tid = db.add_task(desc, assigned_to=assigned)
            db.write_log(self.agent, 'assign', f"Assigned '{desc}' to {assigned}", payload={'task_id': tid})
            if assigned == 'charlie':
                await self.charlie.receive_task(tid, desc)
                # after Charlie finishes, invoke Quinn
                review = await self.quinn.review_task(self.charlie.last_work_dir)
                if review.get('status') == 'PASS':
                    db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), tid))
                    db.conn.commit()
                    db.write_log(self.agent, 'complete', f'Task {tid} passed QA')
                else:
                    # handle up to 3 retries
                    retries = 0
                    while retries < 3 and review.get('status') != 'PASS':
                        retries += 1
                        db.write_log(self.agent, 'reassign', f'Retrying Charlie (attempt {retries})')
                        await self.charlie.receive_task(tid, desc, retry=True)
                        review = await self.quinn.review_task(self.charlie.last_work_dir)
                        if review.get('status') == 'PASS':
                            db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), tid))
                            db.conn.commit()
                            break
                    if review.get('status') != 'PASS':
                        db.write_log(self.agent, 'escalate', 'QA failed after retries', status='warning')
                        # escalate to Henry by creating a task
                        db.add_task(f'Escalation: QA failed for task {tid}', assigned_to='henry')
            elif assigned == 'scout':
                await self.scout.receive_task(tid, desc)
                db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), tid))
                db.conn.commit()
                db.write_log(self.agent, 'complete', f'Scout finished task {tid}')

    async def decompose(self, description: str):
        # Use LLM to decompose into subtasks with assignments
        try:
            prompt = ("You are Ralph, the Engineering Manager for FORGE. Decompose the following task into a list of subtasks. "
                      "For each subtask, provide 'desc' and 'assigned' (choose one of: 'charlie' or 'scout'). Return valid JSON: {\"subtasks\": [ {\"desc\":..., \"assigned\":...} ] }\n\nTask:\n" + description)
            out = await call_model(self.agent, prompt, system_prompt=RALPH_PROMPT)
            parsed = try_parse_json(out)
            if parsed and 'subtasks' in parsed:
                return parsed['subtasks']
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # fallback heuristic
        out = []
        if 'research' in description.lower() or 'audit' in description.lower():
            out.append({'assigned':'scout', 'desc': f"Research: {description}"})
        else:
            out.append({'assigned':'charlie', 'desc': f"Implement: {description}"})
            out.append({'assigned':'scout', 'desc': f"Research for: {description}"})
        return out
