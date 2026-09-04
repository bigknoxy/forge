"""Test-suite wide fixtures.

Ensures tests never depend on the production default of ``/data`` (which is
not writable on CI runners or most dev machines). Setting ``DATA_DIR`` here,
at module import time, guarantees it is in place before pytest collects any
test module -- some test modules read ``DATA_DIR``/compute DB paths at
import time, and ``forge_cli.py`` is invoked in subprocesses that inherit
``os.environ``.
"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="forge-test-data-"))
