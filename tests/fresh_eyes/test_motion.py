import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError, run
from motion import analyze, diffs, extract_gray, flags

W, H = 9, 16


def frame(v):
    return bytes([v]) * (W * H)


def ramp(k):  # a frame whose content shifts with k (continuous motion)
    return bytes(((x + k * 7) % 256) for x in range(W * H))


def even_times(n, fps=30.0):
    return [i / fps for i in range(n)]


def types(fl):
    return sorted(f["type"] for f in fl)


class FlagsTest(unittest.TestCase):
    def run_flags(self, frames, times=None, **kw):
        d1, d2 = diffs(frames)
        return flags(d1, d2, times or even_times(len(frames)), **kw)

    def test_static_no_flags(self):
        self.assertEqual(self.run_flags([frame(200)] * 20), [])

    def test_single_frame_video_no_flags(self):
        self.assertEqual(self.run_flags([frame(200)], [0.0]), [])

    def test_single_frame_flash_is_flicker(self):
        fl = self.run_flags([frame(200)] * 10 + [frame(20)] + [frame(200)] * 10)
        self.assertEqual(types(fl), ["flicker"])
        self.assertEqual(fl[0]["frames"], [9, 10, 11])

    def test_jump_after_rest(self):
        self.assertEqual(types(self.run_flags([frame(200)] * 12 + [frame(60)] * 12)), ["jump_after_rest"])

    def test_first_change_is_not_a_jump(self):
        # VFR video starts at the first change: an instant reaction to the action is not a layout jump
        self.assertEqual(self.run_flags([frame(200), frame(60)], [0.0, 0.5]), [])

    def test_long_transition(self):
        fl = self.run_flags([ramp(k) for k in range(30)], ceiling_ms=500)
        self.assertIn("long_transition", types(fl))

    def test_short_transition_ok(self):
        frames = [frame(200)] * 5 + [ramp(k) for k in range(6)] + [ramp(5)] * 12
        self.assertNotIn("long_transition", types(self.run_flags(frames, ceiling_ms=500)))

    def test_never_settles(self):
        frames = [ramp(k) for k in range(58)]
        fl = self.run_flags(frames, even_times(58), ceiling_ms=5000, record_s=2.0)
        self.assertEqual(types(fl), ["never_settles"])

    def test_settled_when_video_ends_early(self):
        frames = [ramp(k) for k in range(10)]
        self.assertEqual(self.run_flags(frames, even_times(10), ceiling_ms=5000, record_s=2.0), [])


class VideoTest(unittest.TestCase):
    def make_video(self, d, flash_frame=15):
        p = os.path.join(d, "v.mkv")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=90x160:d=1:r=30",
             "-vf", "drawbox=x=10:y=10:w=60:h=60:color=black:t=fill:enable='eq(n,%d)'" % flash_frame,
             "-c:v", "ffv1", p])
        return p

    def test_extract_gray_has_times(self):
        with tmpdir() as d:
            frames, times, w, h = extract_gray(self.make_video(d))
            self.assertEqual(len(frames), len(times))
            self.assertGreaterEqual(len(frames), 29)
            self.assertEqual(w, 90)
            self.assertEqual(len(frames[0]), w * h)

    def test_empty_video_raises(self):
        with tmpdir() as d:
            p = os.path.join(d, "empty.mp4")
            open(p, "wb").close()
            with self.assertRaises(FEError):
                extract_gray(p)

    def test_analyze_flash_writes_report_and_sheet(self):
        with tmpdir() as d:
            out = os.path.join(d, "pack")
            rep = analyze(self.make_video(d), out)
            self.assertIn("flicker", [f["type"] for f in rep["flags"]])
            self.assertTrue(os.path.exists(os.path.join(out, "motion.json")))
            self.assertTrue(rep["sheets"] and all(os.path.exists(s) for s in rep["sheets"]))


if __name__ == "__main__":
    unittest.main()
