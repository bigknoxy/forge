import os
import asyncio
from memory.database import db
from tools.shell_tools import run_shell
from datetime import datetime
from tools.file_tools import list_directory, read_file
from tools.llm import call_model, try_parse_json
from tools.prompts import QUINN_PROMPT

AGENT_NAME = 'quinn'

class Quinn:
    def __init__(self):
        self.agent = AGENT_NAME

    async def review_task(self, work_dir: str):
        db.write_log(self.agent, 'review_start', f'Reviewing {work_dir}')
        path = os.path.join('/workspace', work_dir)
        if not os.path.isdir(path):
            db.write_log(self.agent, 'no_workdir', work_dir, status='warning')
            return {'status':'NEEDS_REVISION','issues':['work dir missing']}
        # Run pytest with PYTHONPATH set to the project path so tests can import top-level modules
        env = {'PYTHONPATH': path}
        code, out, err = run_shell(f'pytest -q {path} --disable-warnings -q', timeout=30, env=env)
        # Collect files for model review
        files = []
        for root, dirs, filenames in os.walk(path):
            for fn in filenames:
                fpath = os.path.join(root, fn)
                try:
                    rel = os.path.relpath(fpath, '/workspace')
                    files.append({'path': rel, 'content': read_file(rel)})
                except Exception:
                    pass
        # Ask the review model to analyze code and test output
        try:
            prompt = ("You are Quinn, a QA reviewer. Given the project files and pytest output, produce a JSON with keys: status (PASS or NEEDS_REVISION), issues (array), review (text).\n\n"
                      f"Files: {[(f['path']) for f in files]}\n\nPytest output:\n{out}\n{err}\n\nProvide actionable line-level feedback where possible.")
            out_text = await call_model(self.agent, prompt, system_prompt=QUINN_PROMPT)
            parsed = try_parse_json(out_text)
            if parsed and parsed.get('status')=='PASS':
                db.write_log(self.agent, 'review_pass', work_dir)
                return {'status':'PASS','output':parsed.get('review','')}
            elif parsed:
                db.write_log(self.agent, 'review_fail', work_dir, status='warning', payload={'report':parsed})
                return {'status':'NEEDS_REVISION','report':parsed}
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # Fallback to test results
        if code == 0:
            db.write_log(self.agent, 'review_pass', work_dir)
            return {'status':'PASS','output':out}
        else:
            report = out + '\n' + err
            db.write_log(self.agent, 'review_fail', work_dir, status='warning', payload={'report':report})
            return {'status':'NEEDS_REVISION','report':report}
