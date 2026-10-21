#!/usr/bin/env python3
"""Find motion problems in a screen recording: flicker, layout jump after rest, too-long transition,
a screen that never settles. Works on native (variable-frame-rate) frames with their PTS: emulator
recordings only contain frames when the screen changes, and resampling would hide one-frame flashes.

CLI: motion.py --video V --out DIR [--ceiling-ms 500] [--record-s S]
"""
import argparse
import json
import os

from fe_common import FEError, FFMPEG, cli, run, write_json
from pixels import tile

REST_EPS = 0.004      # mean |Δ| below this between frames = no visible change
MOVE_EPS = 0.02       # mean |Δ| above this = a clear change
FLICKER_RATIO = 0.35  # frame i+2 is this close to frame i while i→i+1 changed a lot: a flash
REST_S = 0.3          # this long without change counts as "settled"
FLASH_S = 0.25        # a flash returns within this time
DEFAULT_CEILING_MS = 500


def _probe_times(video):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
               "-of", "csv=p=0", video]).stdout.decode()
    times = []
    for line in out.split():
        try:
            times.append(float(line.strip(",")))
        except ValueError:
            continue
    return times


def extract_gray(video, width=90, start=None, end=None):
    """Return (frames as gray bytes, pts seconds, w, h) at native frame timing."""
    if not os.path.exists(video) or os.path.getsize(video) == 0:
        raise FEError("no video at %s" % video)
    dims = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x", video]).stdout.decode().split()
    if not dims:
        raise FEError("no video stream in %s" % video)
    vw, vh = (int(v) for v in dims[0].split("x"))
    h = max(2, int(round(vh * width / float(vw) / 2.0)) * 2)
    raw = run(FFMPEG + ["-i", video, "-fps_mode", "passthrough", "-vf", "scale=%d:%d,format=gray" % (width, h),
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=120).stdout
    size = width * h
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    times = _probe_times(video)
    n = min(len(frames), len(times))
    if n == 0:
        raise FEError("no frames in %s" % video)
    pairs = [(f, t) for f, t in zip(frames[:n], times[:n])
             if (start is None or t >= start) and (end is None or t <= end)]
    if not pairs:
        raise FEError("no frames between %s and %s in %s" % (start, end, video))
    return [p[0] for p in pairs], [p[1] for p in pairs], width, h


def _mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * len(a))


def diffs(frames):
    d1 = [_mad(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    d2 = [_mad(frames[i], frames[i + 2]) for i in range(len(frames) - 2)]
    return d1, d2


def _rest_before(i, d1, times):
    """Seconds the screen stood still right before step i (the change from frame i to i+1)."""
    k = i
    while k > 0 and d1[k - 1] < REST_EPS:
        k -= 1
    return times[i + 1] - times[k] if k < i or times[i + 1] - times[i] >= REST_S else times[i + 1] - times[i]


def _rest_after(i, d1, times):
    """Seconds the screen stood still right after step i; None means it stayed still to the end."""
    m = i + 1
    while m < len(d1) and d1[m] < REST_EPS:
        m += 1
    if m >= len(d1):
        return None
    return times[m] - times[i + 1] if m > i + 1 else times[i + 2] - times[i + 1]


def flags(d1, d2, times, ceiling_ms=DEFAULT_CEILING_MS, record_s=None, t0_s=0.0):
    found = []
    n = len(times)
    for i in range(len(d2)):
        if d1[i] >= MOVE_EPS and d2[i] < FLICKER_RATIO * d1[i] and times[i + 2] - times[i] <= FLASH_S:
            found.append({"type": "flicker", "t": round(times[i + 1], 3), "frames": [i, i + 1, i + 2]})
    for i in range(1, len(d1)):
        if d1[i] < MOVE_EPS:
            continue
        after = _rest_after(i, d1, times)
        if _rest_before(i, d1, times) >= REST_S and (after is None or after >= REST_S):
            found.append({"type": "jump_after_rest", "t": round(times[i + 1], 3), "frames": [i, i + 1]})
    i = 0
    while i < len(d1):
        if d1[i] <= REST_EPS:
            i += 1
            continue
        start = times[i + 1] if times[i + 1] - times[i] >= REST_S else times[i]
        j = i
        while j + 1 < len(d1) and d1[j + 1] > REST_EPS and times[j + 2] - times[j + 1] < REST_S:
            j += 1
        if (times[j + 1] - start) * 1000 > ceiling_ms:
            found.append({"type": "long_transition", "t": round(start, 3),
                          "frames": [i, j + 1], "ms": round((times[j + 1] - start) * 1000)})
        i = j + 1
    # t0_s: when the first frame was taken, in recording time (Android timestamps start at the first change)
    if record_s is not None and n >= 2 and d1[-1] > REST_EPS and record_s - (times[-1] + t0_s) < REST_S:
        found.append({"type": "never_settles", "t": round(times[-1], 3), "frames": [n - 2, n - 1]})
    return sorted(found, key=lambda f: f["t"])


def extract_frames(video, out_dir, start=None, end=None, height=300, max_frames=8):
    os.makedirs(out_dir, exist_ok=True)
    vf = "scale=-2:%d" % height
    if start is not None or end is not None:
        vf = "select='between(t,%f,%f)',%s" % (start or 0.0, end if end is not None else 1e9, vf)
    run(FFMPEG + ["-i", video, "-fps_mode", "passthrough", "-vf", vf, os.path.join(out_dir, "f_%04d.png")],
        timeout=120)
    files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("f_"))
    if len(files) > max_frames:
        step = (len(files) - 1) / float(max_frames - 1)
        keep = {files[int(round(k * step))] for k in range(max_frames)}
        for f in files:
            if f not in keep:
                os.remove(f)
        files = sorted(keep)
    return files


def analyze(video, out_dir, ceiling_ms=DEFAULT_CEILING_MS, record_s=None, t0_s=0.0):
    frames, times, _, _ = extract_gray(video)
    d1, d2 = diffs(frames)
    found = flags(d1, d2, times, ceiling_ms, record_s, t0_s)
    sheets = []
    windows = [(f["t"] - 0.3, f["t"] + 0.3) for f in found[:4]]
    if not windows and len(frames) >= 2:
        windows = [(None, None)]
    for k, (a, b) in enumerate(windows):
        files = extract_frames(video, os.path.join(out_dir, "frames_%d" % k), a, b)
        if files:
            sheets.append(tile(files, os.path.join(out_dir, "sheet_%d.png" % k)))
    report = {"frames": len(frames), "duration": round(times[-1] - times[0], 3), "flags": found,
              "sheets": sheets}
    write_json(os.path.join(out_dir, "motion.json"), report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ceiling-ms", type=int, default=DEFAULT_CEILING_MS)
    ap.add_argument("--record-s", type=float)
    a = ap.parse_args()
    rep = analyze(a.video, a.out, a.ceiling_ms, a.record_s)
    print(json.dumps({"flags": [f["type"] for f in rep["flags"]], "sheets": rep["sheets"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
