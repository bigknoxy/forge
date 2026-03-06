import os
import asyncio
from tools.search_tools import web_search, fetch_url, save_research_report
from memory.database import db
from datetime import datetime
from tools.llm import call_model, try_parse_json
from tools.prompts import SCOUT_PROMPT

AGENT_NAME = 'scout'

class Scout:
    def __init__(self):
        self.agent = AGENT_NAME

    async def receive_task(self, task_id: int, description: str):
        db.write_log(self.agent, 'receive_task', description, payload={'task_id':task_id})
        # Perform a web search
        query = description
        try:
            results = web_search(query, max_results=5)
        except Exception as e:
            results = []
            db.write_log(self.agent, 'search_error', str(e), status='error')
        # Use model to synthesize a structured report
        try:
            hits = '\n'.join([f"- {r.get('title')} - {r.get('href')}" for r in results])
            prompt = f"You are Scout. Write a clear markdown research report for the task: {description}\n\nSearch results:\n{hits}\n\nProduce a markdown report with summary, key findings, and recommended next steps."
            out = await call_model(self.agent, prompt, system_prompt=SCOUT_PROMPT)
            # If model returns markdown, save it
            fname = f"research/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_research.md"
            from tools.file_tools import write_file
            write_file(fname, out)
            db.write_log(self.agent, 'write_report', fname)
            db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), task_id))
            db.conn.commit()
            return fname
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # Fallback
        content = f"# Research Report\n\nTask: {description}\n\nResults:\n\n"
        for r in results:
            content += f"- {r.get('title')} - {r.get('href')}\n"
        filename = f"research/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_research.md"
        from tools.file_tools import write_file
        write_file(filename, content)
        db.write_log(self.agent, 'write_report', filename)
        db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), task_id))
        db.conn.commit()
        return filename
