# fresh-eyes

A Claude Code plugin that tests a running iOS or Android app the way a picky human tester and designer would,
then writes what it finds to your backlog with a screenshot and steps to reproduce.

Scripted UI tests check that a button exists and a flow completes. fresh-eyes looks at what a person would
notice: text cut off mid-word at a large font, a sheet that opens twice after a double tap, a menu that stays
open after Back, a field hidden under the keyboard, a dark theme that only half switches, an icon that means
something else on the next screen, a tap target smaller than a finger, buttons drawn almost touching, a blank
band between blocks, a block far bigger than its content, an error in the log while the screen looks fine. It
also proposes improvements, small ones too (a word, a gap, a step that could go), kept apart from bugs.

Built for Expo / React Native apps (Expo Router screens). Navigation and capture work with any app on an
Android emulator or iOS simulator.

## Install

```
/plugin marketplace add RasulJin/fresh-eyes
/plugin install fresh-eyes@fresh-eyes
```

Then, in your app's repo:

```
/fresh-eyes init                 # once: config, project spec, devices; you confirm the spec
/fresh-eyes quick                # up to 4 screens your branch changed most
/fresh-eyes quick feed,profile   # chosen screens
/fresh-eyes quick BUG-12         # recheck one backlog bug: verified or reopened
/fresh-eyes full                 # every screen, state and probe before a release (resumable)
```

## How it works

1. **Preflight** checks the emulators it owns, the installed build and the backend it talks to. Probes that
   create data only run against a verified staging or local backend. When it builds the app, or you name the
   commit the installed build came from, it exports that code, so reviewers judge the code that is running.
2. **Coverage plan**: screens × states (default, dark, large font, keyboard, small screen) × probes (rapid
   tap, tap outside, Back mid-animation, background and return, swipe inside a list), plus journeys.
3. **Navigator** drives the device through the view hierarchy and saves an evidence pack per cell:
   screenshot, numbered marks, crops of what scripts found suspicious, video and motion analysis, logcat.
4. **Reviewer** compares the evidence with your project's own spec, built from your design files, decisions
   and commits, and writes findings.
5. **Verifier** tries to disprove each finding and asks for fresh frames or a repeat when the evidence is thin.
   A finding the run has to ask you about is never counted as confirmed.
6. **Verdict** comes from code, not from a model: BLOCK, ISSUES, INCOMPLETE or CLEAN. A run never says CLEAN
   while any planned cell is unchecked.
7. You confirm which findings are real. Those go to the backlog; the rest go to `.fresh-eyes/known.md` and are
   not raised again.

fresh-eyes only changes devices whose names start with `fresh-eyes`. Your own emulators and phones are never
touched. Test credentials stay in environment variables and never reach prompts, files or reports.

## Measured results

Measured on one production app (Voiova, Expo / React Native, Android), with the plugin's own benchmark
(`/fresh-eyes bench`): known bugs are planted back by testing builds where they still live, three attempts each,
plus a run on the fixed build. A blind judge decides matches.

| Check | Target | Result |
|---|---|---|
| Live bugs from the backlog caught (≥2 of 3 attempts) | ≥6 of 8 | 8 of 8 |
| Older bugs from past fixes caught | ≥3 of 5 | 5 of 5 (the plugin was tuned on these misses) |
| False "CLEAN" while a planned bug went uncaught | 0 | 0 in every run |
| False findings among confirmed, full run of all screens | ≤25% | 36% on the first run, 13% (20% counting disputed ones) after the fixes, on a second independent run |

Live and older bugs were measured on versions 0.5.2 and 0.5.3; the last two live cases on 0.5.3, two attempts
each. The false-finding rate was labelled by checking each confirmed finding against the code and commit
history, not yet by the app's owner, and the second full run saw the app in English only. A full run of 17 screens and 2 journeys (104 cells) takes about 5 hours and
$55–65 of API usage; a quick run takes about 20 minutes and $4 per screen.

## Limits

- iOS is supported, but full mode has not been run on iOS yet, and the plugin has been tried on one app.
- Emulators are not phones: smoothness numbers are a regression signal only. Push notifications, OEM
  background killing and real networks need a device.
- Recall varies between runs: a bug found in one full run can be missed in the next. `/fresh-eyes full twice`
  merges two independent passes.
- Findings are judged by models. The verifier removes most noise, and you still confirm each one before it
  reaches the backlog.

## Docs

Full documentation (requirements, config, cost, benchmark format): [plugins/fresh-eyes/README.md](plugins/fresh-eyes/README.md).
Config reference: [config-format.md](plugins/fresh-eyes/skills/fresh-eyes/references/config-format.md).

## License

MIT
