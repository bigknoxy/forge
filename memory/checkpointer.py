import json
from memory.database import db

class Checkpointer:
    """Simple wrapper to store supervisor state in the checkpoints table."""
    KEY_PREFIX = 'supervisor.'

    def save(self, name: str, state: dict):
        db.set_state(self.KEY_PREFIX + name, json.dumps(state))

    def load(self, name: str):
        val = db.get_state(self.KEY_PREFIX + name)
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

checkpointer = Checkpointer()
