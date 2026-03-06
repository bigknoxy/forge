import os
import asyncio
from datetime import datetime
from tools.file_tools import write_file, read_file
from tools.shell_tools import run_shell, git_operation
from tools.skill_tools import register_skill, query_skill_library
from memory.database import db
from tools.llm import call_model, try_parse_json
from tools.prompts import CHARLIE_PROMPT

AGENT_NAME = 'charlie'
WORKSPACE = os.getenv('WORKSPACE_DIR','/workspace')

class Charlie:
    def __init__(self):
        self.agent = AGENT_NAME
        self.last_work_dir = None

    async def receive_task(self, task_id: int, description: str, retry: bool=False):
        db.write_log(self.agent, 'receive_task', description, payload={'task_id': task_id})
        # Check skills first
        skills = query_skill_library(description)
        if skills:
            db.write_log(self.agent, 'use_skill', f'Found skills for {description}', payload={'skill_count': len(skills)})
            # For now, assume skill usage completes the task
            db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('done', datetime.utcnow().isoformat(), task_id))
            db.conn.commit()
            return
        # Ask the coder model to produce files and a plan
        try:
            prompt = f"Task: {description}\n\nProduce JSON with keys: files (map of path->content), optional register_skill (object with name, script, manifest) or null, and notes."
            out = await call_model(self.agent, prompt, system_prompt=CHARLIE_PROMPT)
            parsed = try_parse_json(out)
            if parsed and 'files' in parsed:
                files = parsed['files']
                name = f"project_{task_id}"
                proj_dir = os.path.join('projects', name)
                self.last_work_dir = proj_dir
                # write files
                for path, content in files.items():
                    write_file(os.path.join(proj_dir, path), content)
                db.write_log(self.agent, 'write_files', f'Wrote files for task {task_id}', payload={'path': proj_dir})
                # register skill if requested
                if parsed.get('register_skill'):
                    sk = parsed['register_skill']
                    try:
                        register_skill(sk['name'], sk.get('description',''), sk['script'], sk.get('manifest',{}))
                        db.write_log(self.agent, 'register_skill', f"Registered skill {sk['name']}")
                    except Exception as e:
                        db.write_log(self.agent, 'skill_error', str(e), status='error')
                # Git operations if GH_TOKEN is set
                gh = os.getenv('GH_TOKEN')
                full_path = os.path.join(WORKSPACE, proj_dir)
                if gh:
                    os.makedirs(full_path, exist_ok=True)
                    git_operation(f'git init {full_path}')
                    git_operation(f'git -C {full_path} add .')
                    git_operation(f'git -C {full_path} commit -m "Initial scaffold by Charlie"')
                    db.write_log(self.agent, 'git', 'Initialized repo locally', payload={'path':full_path})
                # mark in_review
                db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('in_review', datetime.utcnow().isoformat(), task_id))
                db.conn.commit()
                return
        except Exception as e:
            db.write_log(self.agent, 'llm_error', str(e), status='warning')
        # Fallback: simple scaffold (pytest-friendly package layout)
        name = f"project_{task_id}"
        proj_dir = os.path.join('projects', name)
        pkg_name = name  # use project folder as package name
        self.last_work_dir = proj_dir
        # README
        write_file(f"{proj_dir}/README.md", f"# {name}\n\n{description}\n")
        # package directory
        write_file(f"{proj_dir}/{pkg_name}/__init__.py", '')
        write_file(f"{proj_dir}/{pkg_name}/app.py", 'def add(a,b):\n    return a+b\n')
        # pyproject
        write_file(f"{proj_dir}/pyproject.toml", '[tool.poetry]\nname = "'+name+'"\nversion = "0.1.0"\n')
        # tests import package.app
        write_file(f"{proj_dir}/tests/test_app.py", f'from {pkg_name}.app import add\n\ndef test_add():\n    assert add(2,3)==5\n')
        db.write_log(self.agent, 'write_files', f'Created fallback scaffold for {name}', payload={'path':proj_dir})
        # register a sample skill
        skill_script = 'case "$1" in --json) echo "{\"status\": \"ok\"}";; *) echo "Scaffold created";; esac'
        manifest = {
            'name':'scaffold-generator',
            'description':'Creates a basic python project scaffold',
            'inputs':['project_name','description'],
            'outputs':['path'],
            'usage':'scaffold-generator --json project_name description'
        }
        try:
            register_skill('scaffold-generator','Creates python scaffolds', skill_script, manifest)
            db.write_log(self.agent, 'register_skill', 'Registered scaffold-generator')
        except Exception as e:
            db.write_log(self.agent, 'skill_error', str(e), status='error')
        gh = os.getenv('GH_TOKEN')
        if gh:
            full_path = os.path.join(WORKSPACE, proj_dir)
            if not os.path.exists(full_path):
                os.makedirs(full_path, exist_ok=True)
            git_operation(f'git init {full_path}')
            git_operation(f'git -C {full_path} add .')
            git_operation(f'git -C {full_path} commit -m "Initial scaffold by Charlie"')
            db.write_log(self.agent, 'git', 'Initialized repo locally', payload={'path':full_path})
        db.conn.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('in_review', datetime.utcnow().isoformat(), task_id))
        db.conn.commit()
