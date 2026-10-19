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


def make_png(path, w, h, bg=(255, 255, 255), rects=()):
    """Write a PNG; rects are (x, y, w, h, (r, g, b), radius) drawn in order, with rounded corners."""
    from fe_common import write_png
    buf = bytearray(bytes(bg) * (w * h))
    for (rx, ry, rw, rh, color, r) in rects:
        for y in range(max(0, ry), min(h, ry + rh)):
            for x in range(max(0, rx), min(w, rx + rw)):
                cx = rx + r if x < rx + r else (rx + rw - 1 - r if x > rx + rw - 1 - r else x)
                cy = ry + r if y < ry + r else (ry + rh - 1 - r if y > ry + rh - 1 - r else y)
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    i = (y * w + x) * 3
                    buf[i:i + 3] = bytes(color)
    write_png(path, w, h, bytes(buf))
    return path
