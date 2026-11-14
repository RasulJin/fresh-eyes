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


class KeyboardTest(unittest.TestCase):
    """The chat field riding the keyboard on a 360x640 dp phone (fe10-verify J7 and its re-check 6a), downscaled."""
    def pack(self, d, **videos):
        import shutil
        from fe_common import write_json
        from tests.fresh_eyes.helpers import FIXTURES
        write_json(os.path.join(d, "meta.json"), {"screen_wh": [720, 1280]})
        write_json(os.path.join(d, "nodes.json"), [{"id": "n109", "focused": True, "bounds": [58, 570, 532, 72]}])
        for name, video in videos.items():
            os.makedirs(os.path.join(d, name))
            shutil.copy(os.path.join(FIXTURES, "keyboard", video + ".mp4"), os.path.join(d, name, "video.mp4"))
        return d

    def test_field_that_stops_and_jumps_again(self):  # up 348 px, a 170 ms stop, then 60 px more
        from motion import keyboard
        with tmpdir() as d:
            rep = keyboard(self.pack(d, **{"kb-show": "show-double-jump", "kb-hide": "hide-one-move"}))
            self.assertEqual(rep["flags"], ["kb-show: double_jump"])
            self.assertEqual([m["dy_px"] < -40 for m in rep["kb-show"]["moves"]], [True, True])
            self.assertEqual(rep["kb-hide"]["jumps"], 1)
            self.assertTrue(os.path.exists(rep["kb-show"]["sheet"]))

    def test_field_covered_by_the_rising_keyboard(self):  # one move, but two frames with the field under the keys
        from motion import keyboard
        with tmpdir() as d:
            rep = keyboard(self.pack(d, **{"kb-show": "show-lag"}))
            self.assertEqual(rep["flags"], ["kb-show: lag"])
            self.assertEqual((rep["kb-show"]["jumps"], rep["kb-show"]["covered_frames"]), (1, 2))
            self.assertTrue(os.path.isfile(os.path.join(d, "keyboard.json")))

    def test_a_settling_tail_after_a_stop_is_not_a_second_jump(self):  # J11 at full size: 242 -> 244 after 227 ms
        from motion import jumps
        times = [0.0, 0.027, 0.484, 0.567, 0.59, 0.629, 0.662, 0.682, 0.724, 0.765, 0.804, 0.86, 0.951, 1.178, 1.47]
        ys = [142, 142, 142, 142, 142, 142, 142, 147, 219, 225, 236, 238, 242, 244, 244]
        moves, covered = jumps(ys, times, 18)
        self.assertEqual(([m[2:] for m in moves], covered), ([[142, 242]], 0))

    def test_needs_a_focused_field_and_a_video(self):
        from motion import keyboard
        from fe_common import write_json
        with tmpdir() as d:
            self.pack(d)
            with self.assertRaisesRegex(FEError, "kb-show"):
                keyboard(d)
            write_json(os.path.join(d, "nodes.json"), [{"id": "n1", "focused": False, "bounds": [0, 0, 9, 9]}])
            with self.assertRaisesRegex(FEError, "no focused field"):
                keyboard(d)
