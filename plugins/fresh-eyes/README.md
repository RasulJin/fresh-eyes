# fresh-eyes

Tests a running iOS/Android app the way a picky human tester and designer would, and writes what it finds to
your backlog. It goes after the bugs that scripted UI tests walk past: clipped or cut text, overlapping layers,
stacked sheets after a double tap, menus that do not close, a jump mid-animation, a field hidden under the
keyboard, a dark theme that only half switches, inconsistent icons, copy that contradicts itself. It also
proposes UX improvements.

Built for Expo / React Native projects (Expo Router screens). Navigation and capture work with any app on an
Android emulator or iOS simulator.

## What a run does

1. **Preflight**: the devices it owns are up, the app is installed, the soft keyboard and animations are on,
   and the build talks to the backend you named (mutating probes such as "post a comment" only run on a
   verified staging or local backend).
2. **Coverage plan**: screens × states (default, dark, large font, keyboard, small screen…) × probes (rapid
   tap, tap outside, Back during an animation, background and return, swipe inside a list…), plus journeys.
3. **Navigator** (Sonnet) drives the device by the view hierarchy and saves an evidence pack per cell:
   screenshot, numbered marks, crops of everything the scripts found suspicious, video and motion analysis for
   transitions.
4. **Reviewer** (Opus) looks at the evidence against your project's own spec (`.fresh-eyes/spec.md`, built
   from your design files, decisions and commits) and writes findings.
5. **Verifier** (Opus) tries to disprove each finding, asking the navigator for fresh frames or repeats when
   the evidence is thin.
6. **Verdict** comes from code, never from a model: BLOCK, ISSUES, INCOMPLETE (something was not checked) or
   CLEAN. A run never says "all good" while any planned cell is unchecked.
7. You confirm which findings are real; those go to the backlog with a crop, and the rest go to
   `.fresh-eyes/known.md` so they are not raised again.

## Requirements

- macOS, Python 3.9+ (standard library only), `ffmpeg` with the `ffv1` codec (`brew install ffmpeg`).
- Android: the Android SDK (`emulator`, `platform-tools`) and a system image; fresh-eyes creates its own AVDs
  `fresh-eyes-pixel` and `fresh-eyes-small` (about 4 GB and 2 GB of free disk).
- iOS: Xcode with an iOS simulator runtime, and the [Maestro](https://maestro.dev) MCP server. fresh-eyes
  creates the simulators `fresh-eyes iPhone 17` and `fresh-eyes iPhone 16e`.
- Your app built for the emulator/simulator and pointed at a staging or local backend.

fresh-eyes only changes devices whose names start with `fresh-eyes`. Your own emulators and phones are never
touched.

## Install

```
/plugin marketplace add RasulJin/fresh-eyes
/plugin install fresh-eyes@fresh-eyes
```

## Use

```
/fresh-eyes init                 # once per project: config, spec, devices; you confirm the spec
/fresh-eyes quick                # up to 4 screens your branch changed most (diff against the default branch)
/fresh-eyes quick BUG-12         # recheck one backlog bug: verified or reopened
/fresh-eyes quick feed,profile   # chosen screens
/fresh-eyes full                 # every screen, state and probe, before a release (resumable)
/fresh-eyes full twice           # two independent passes, findings merged: one pass misses about a quarter
```

A long run asks for many tool calls. `init` prints the allow-rules to add to `.claude/settings.local.json`.

Files it keeps in your repo: `.fresh-eyes/config.json`, `spec.md`, `known.md`, `examples/` (commit them);
`runs/`, `cache/` and `bench/runs/` are git-ignored.

## Cost

The skill is invoked only by you (`disable-model-invocation`), so it adds about 230 tokens to a session until
you call it. During a run the orchestrator keeps screenshots and videos out of the main conversation;
subagents exchange files and reply in at most ten lines. Screens reach the navigator as compact text
(~1.5k characters), not as hierarchy JSON or images.

- Each agent has a tool allowlist, so it starts at about 7k tokens. A general-purpose agent in a session with
  many connectors and plugins starts at 30–80k and re-reads that on every turn.
- `quick` checks at most 4 screens and skips neighbors unless you name them. The report lists what it skipped,
  and a run that skipped something ends INCOMPLETE, never CLEAN.
- Each screen gets its own verifier, which only sees that screen's findings.
- Run fresh-eyes in a new session, or right after `/compact`: every orchestrator step re-reads the whole
  conversation.

## Benchmark

To check that fresh-eyes catches the bugs your project actually had, list them in
`.fresh-eyes/bench/cases.json`, each with what a person sees:

```json
[{"id": "BUG-7", "group": "live", "kind": "bug", "screen": "profile-trips", "platform": "android",
  "also_screens": [], "expect": {"description": "the trips grid is cut: the third row shows as a strip",
  "bbox": null}}]
```

Run `/fresh-eyes bench`. It needs three ordinary `quick` runs over the cases' screens on a build where the bugs
still live, and one run on a build with the fixes. Code collects, per case and run, the findings on the case's
screen (and area), and a `fresh-eyes:judge` decides which of them, if any, is the case's defect; it is not told
which runs had the fixes. A case is caught when it is matched in at least two of three attempts; a match on the
fixed build is a false positive; `false_clean` lists runs whose verdict was CLEAN while a planned case went
uncaught, and must stay empty.

## Limits

- Emulators and simulators are not real phones: smoothness numbers are a regression signal only; push,
  OEM background killing and real network conditions need a device.
- iOS: offline is not automated; rapid taps land ~225 ms apart (the Maestro driver serializes touches), so
  sub-100 ms races are probed on Android only; a React Native `Pressable` without `accessibilityRole` is not
  seen as tappable.
- Pinch and other multi-finger gestures are not probed.
- Findings are judged by models: the verifier removes most noise, and you still confirm each one before it
  reaches the backlog.

## License

MIT
