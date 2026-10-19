"""Shared test utilities: puts the plugin scripts on sys.path."""
import contextlib
import os
import shutil
import sys
import tempfile

SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "plugins", "fresh-eyes", "skills", "fresh-eyes", "scripts"))
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


@contextlib.contextmanager
def tmpdir():
    """Temp dir whose name has a space and Cyrillic, like real user paths."""
    base = tempfile.mkdtemp()
    d = os.path.join(base, "fe тест dir")
    os.makedirs(d)
    try:
        yield d
    finally:
        shutil.rmtree(base, ignore_errors=True)
