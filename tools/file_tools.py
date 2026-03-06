import os
from typing import List
from pathlib import Path

WORKSPACE = os.getenv('WORKSPACE_DIR', '/workspace')

def read_file(path: str) -> str:
    p = Path(WORKSPACE) / path.lstrip('/')
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(path: str, content: str):
    p = Path(WORKSPACE) / path.lstrip('/')
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)

def list_directory(path: str) -> List[str]:
    p = Path(WORKSPACE) / path.lstrip('/')
    if not p.exists():
        return []
    return [str(child.name) for child in p.iterdir()]
