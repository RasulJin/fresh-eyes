# Motion: recording and reading transitions

Screenshots are taken after the screen settles, so they never show a transition. Motion bugs need video.

## Capture

- Wrap the action: `python3 $S/capture.py around --platform P --device ID --out PACK --settle 2.5
  --ceiling-ms <spec motion ceiling> -- <action command>`.
- Android recordings (`screenrecord`) are variable frame rate: frames exist only when pixels change, about
  10–15 fps on an emulator, and the timeline starts at the first change. A static screen yields one frame.
- iOS recordings come from `simctl io recordVideo --codec=h264`.
- `motion.py` works on native frames with their timestamps; it never resamples (resampling hides one-frame
  flashes).

## Flags (from `motion.json`)

| flag | meaning | typical bug |
|---|---|---|
| `flicker` | a frame differs a lot from its neighbours, and the next frame is back to the previous one within 250 ms | a flash of the old screen, a white frame between screens, a ghost of the previous tab |
| `jump_after_rest` | after ≥300 ms of stillness one single-step change, then stillness again (not the first change) | layout shift after load, a sheet re-docking for one frame, content jumping by the keyboard height |
| `long_transition` | continuous motion longer than the ceiling (spec, default 500 ms) | sluggish transitions, double animations |
| `never_settles` | the video still moves at the end of the recording window | endless spinner, oscillating layout, a stuck spring |

Thresholds: mean frame difference < 0.004 = still, > 0.02 = a clear change.

## Reading contact sheets (`sheet_*.png`)

- 8 frames, left→right, top→bottom, around each flag (or the whole recording when nothing was flagged).
- Check: does every frame make sense on its way from A to B? Is anything drawn twice, in the wrong layer
  (a sheet passing under a card), from the wrong side, or for one frame only?
- Compare with the expected transition in the spec (direction, what moves, duration ceiling, easing intent).
- A flag is a candidate, not a verdict: a fast list scroll can trip `long_transition`; a deliberate
  crossfade can look like a flash. Decide from the frames.

## Smoothness (jank)

- Simulator/emulator frame pacing is not real-device performance. Report smoothness only as a regression
  signal and only when measured with a profiler (Android: `dumpsys gfxinfo <package> framestats` before and
  after a scripted scroll on the same device).
