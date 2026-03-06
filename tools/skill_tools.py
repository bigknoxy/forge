import os
import json
from pathlib import Path
from memory.database import db

WORKSPACE = os.getenv('WORKSPACE_DIR', '/workspace')
SKILLS_DIR = os.path.join(WORKSPACE, 'skills')
os.makedirs(SKILLS_DIR, exist_ok=True)

def query_skill_library(query: str):
    rows = db.query_skills(query)
    return [dict(r) for r in rows]

def register_skill(name: str, description: str, script_content: str, manifest: dict):
    # Create a CLI script in skills dir
    path = os.path.join(SKILLS_DIR, name)
    os.makedirs(path, exist_ok=True)
    script_path = os.path.join(path, name + '.sh')
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write('#!/usr/bin/env bash\n')
        f.write('set -euo pipefail\n')
        f.write(script_content)
    os.chmod(script_path, 0o755)
    # Write manifest
    manifest_path = os.path.join(path, 'skill_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    # Register in DB
    db.register_skill(name, description, path, manifest)
    return {'name':name, 'path': path}
