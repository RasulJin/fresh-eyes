# Report, findings and backlog formats

## Run folder

```
.fresh-eyes/runs/<YYYYMMDD-HHMM>-<mode>/
  preflight.json  coverage.json  spec-stale.json  backlog-index.md
  report.md  imp.md  rejected.md  run-log.md ← the report and its companions (see report.md below)
  score.json                                ← coverage.py score: M1-M12 {value, ok, what}, the judge's M1/M4/M7/M11
  spec/<screen>.md  spec/journey.md         ← spec slices: global rules + the screen and its neighbors
  <platform>/<screen>/<state>-<probe>/      ← one evidence pack per cell
    screen.png screen_1000.png som.png som.json nodes.json hierarchy.txt candidates.json meta.json
    crops/*.png  [video.mp4 motion.json sheet_*.png around.json]  [taps.json]  [logcat.txt]
  <platform>/<screen>/findings.json         ← per screen × platform (reviewer, then verifier);
                                              findings-2.json … when packs were split between reviewers
  <platform>/<screen>/requests.json         ← optional: the verifier asks the navigator for fresh evidence
```

## `findings.json` (`<run>/<platform>/<screen>/findings.json`)

```json
{"reviewed_packs": ["<pack path>", "…"], "findings": [{
  "id": "f1", "kind": "BUG|IMP", "severity": "high|medium|low", "confidence": 0.0,
  "title": "", "screen": "", "platform": "",
  "check": "<geometry check | motion flag | spec rule id | taste>",
  "som": [3], "bbox": [0, 0, 0, 0], "expected": "", "actual": "", "steps": [""], "found_by": "",
  "rule": "spec:<id> | taste", "evidence": ["crops/clipped-1.png"], "log": "<logcat.txt line (app errors only)>",
  "verdict": "pending|confirmed|unsure|rejected", "verify_note": "",
  "same_as": "<findings file relative to the run>#<id> (optional)", "backlog_id": "BUG-N | IMP-N (optional)"}],
 "questions": [{"text": "", "options": [""], "finding": "<id> | <findings file relative to the run>#<id>",
                "class": "spec-text | known | conflict (optional)"}],
 "requests": [{"pack": "", "do": "repeat-probe", "probe": "tap-everything", "why": ""}]}
```

- Text fields (`title`, `expected`, `actual`, `steps`, `verify_note`, `questions`) are in the config's
  `language`; ids, paths, rule ids and on-screen text stay as they are.
- `steps` is how a person repeats the bug from the app's start screen, written for whoever fixes it, e.g.
  `["Open the Feed tab", "Tap «Share» under any post three times quickly", "Press Back once", "Look: the share
  sheet is still open"]`. `found_by` is where the run saw it (`android/post-detail/default/rapid-tap,
  afterback`): for the verifier and the navigator, never instead of `steps`.
- `same_as`: the verifier marks a finding that reports the same defect as another one (split reviewers, a
  journey adding evidence). The verdict leaves it out of the counts only when the finding it points to is
  confirmed, of the same kind and not lower in severity, and it was itself ruled on (never while pending); it
then returns it in `merged`, and the report lists it under that finding.
- `log`: for an app error seen in `logcat.txt`, its most specific line copied as is without the repeat count
  (the exception message, not a stack frame or a bare `Unhandled SoftException`). The verdict counts one line
  once across screens: the most severe confirmed copy stays, the others are returned in `merged`.
- `questions[].finding`: the finding the question is about. `coverage.py verdict` counts a confirmed finding
  with a question as unsure and returns it in `asked`: until the owner answers, it is not a bug.
- `questions[].class` (no `finding`, no BUG behind it, no effect on the verdict): `spec-text`, the app shows
  other words than the spec quotes and `source_root` holds them under a string key, one entry per screen with
  its pairs (`<rule> «<spec>» → «<app>» (<key>)`; is the spec out of date?); `known`, a comment in `source_root`
  calls the choice deliberate (`<screen>: <what> — <file>: «<comment>»`; add it to `known.md`?); `conflict`, what
  a `⚠ conflict` rule leaves to the owner (`<rule id>: <what the frame shows>`; the spec already asks it).
- `requests` (optional, reviewer): what a frame could not settle, for the verifier to have redone
  (verifier.md): `repeat-probe` (a tap or gesture no frame showed the result of), `resnap` (a frame to take
  again: caught mid-load, mid-animation or on the wrong screen), `crop` (a region to look at closer); `finding`
  names the finding it would settle. A BUG at confidence ≤ 0.4 without such a request is a guess: the verdict
  lists it in `warnings` (it stays a request or an IMP).
- `reviewed_packs`: every pack whose evidence the reviewer actually opened (its `som.png`, or its crops plus
  `candidates.json`), and only those. A done cell whose packs no reviewer listed keeps the verdict INCOMPLETE.
- `invariants` (when the slice has a `### Invariants`): `[{"id", "pack", "result": "ok|broken|not shown"}]`, each
  `inv` line the reviewer recomputed (reviewer.md step 4); a `broken` one is also a BUG with the arithmetic.
- `backlog_id` (`BUG-N` or `IMP-N`): the verifier sets it on a confirmed BUG or an IMP that an open entry of
  `backlog-index.md` already describes, with `verify_note` "already open: <id>". A BUG still counts as
  confirmed; the report lists it under "still reproduces" and the backlog gets a recheck line instead of a new
  entry. An IMP with it gets no new entry.
- `rule: taste` (no spec rule behind it) caps `confidence` at 0.6.
- The verifier sets `verdict` and `verify_note`. No BUG reaches the backlog while `pending`; IMPs are proposals
  and go in when the owner picks them. The verifier only rejects an IMP the spec slice or `known.md`
  contradicts; a `pending` IMP is final.

## `requests.json` (verifier → navigator, in the screen folder)

`[{"finding": "f1", "pack": "<path>", "do": "resnap|repeat-probe|crop", "probe": "<id>", "bbox": [..],
"args": "<extra probe flags, optional: --interval-ms 150>", "times": 2, "why": "", "from": "<findings file>"},
{"do": "redo-cell", "cell": "<cell id>", "why": ""}]` (`times`, `why`, `from` optional; `from`: a reviewer's);
the navigator adds `"done": "<path>"` or `"failed": "<why>"` to each entry but `redo-cell`, which the
orchestrator handles (the cell back to `todo`, navigated and reviewed again, then `done`).

## Verdict

Computed only by `coverage.py verdict` (never by a model); the first that applies wins:
- one defect ruled both ways (a finding confirmed with `same_as` to a rejected one or the reverse, or the same
  kind, rule and title confirmed once and rejected once) → `CONFLICT` (РЕШЕНИЯ РАСХОДЯТСЯ): no report yet; the
  verdict's `conflicts` lists the pairs (file#id), each pair goes to one verifier, then the verdict runs again;
  one round only: a pair still ruled both ways turns `unsure` on both sides ("rulings disagree: owner decides"),
  the verdict runs once more and the report is written, the pair under "Look with your own eyes";
  its `reasons` still say what the code would be without the conflict;
- any confirmed BUG of severity high → `BLOCK` (НЕ ВЫПУСКАТЬ);
- any confirmed BUG of severity medium → `ISSUES` (ЕСТЬ ПРОБЛЕМЫ), even when something was not checked;
- any cell not done (`na` too: drop that probe from the config), any done cell whose packs no reviewer listed,
  any BUG still `pending`, any `unsure` BUG of severity high or medium (intermittent, asked, stale spec), a build
  that changed during the run, any unchecked item of this run (quick's screen limit, skipped neighbors) →
  `INCOMPLETE` (НЕ ПРОВЕРЕНО ДО КОНЦА);
- otherwise `CLEAN` (ЧИСТО).

Permanent gaps never block: plan items with `"gap": true`, which no run of this project as configured can
check (a platform without a ready device or build, a state not automated on that platform, a missing hook,
seed, `app_theme` or small device, mutation off). The verdict lists them as "permanent gaps: N". IMPs never
change the verdict, and neither do low unsure findings.

Stale spec: when `spec-stale.json` lists a screen (its code changed after the spec's `Updated` date), a
confirmed finding whose rule cites that screen's section (`spec:<id>…`) turns `unsure`. The verdict's `stale`
lists those findings (file#id, like `asked`); the findings files still say `confirmed`, so the report and the
backlog go by the verdict: they sit under "look with your own eyes", never under bugs and never in the backlog.
Each stale screen gets one question from the verdict's reason line (section ids and the `Updated` date):
"section <id> dated <Updated>, code changed since: are its rules current?".

## `report.md` and its companions (project language)

`report.md` is what the owner reads first: at most 300 lines, each item a pointer; the full texts stay in the
findings files. Beside it: `imp.md` (written by `backlog.py match`, never by hand: every improvement in full,
the new ones numbered with screen, platform, severity, file#id, actual → expected, verify note, question and
crop, then the ones an open entry already proposes a line each with its IMP-N; the owner picks by those
numbers, and match sets that `backlog_id` on the IMPs the verifier left without one), `rejected.md` (one line per rejected finding:
title, screen, kind and severity, file#id, the first sentence of `verify_note`; a `flaky:` one was seen once and
missed by the repeats, M2 does not count it false) and `run-log.md` (each step the
orchestrator took by hand, numbered, what and why; then the test data the run left on the backend; then
`Links (same case)`: each pair of step 10's `findings.py same` `links`, `<copy> → <target> (<target's verdict>)`).

```markdown
# fresh-eyes — <mode> — <date>
**Verdict: <label>** — <reasons, one line each>
Build: <branch/commit>, backend: <api host or "not verified">, devices: <names>
In numbers: BUG <confirmed> / <unsure> / <rejected>, IMP <new> + <already open>, questions <n> · imp.md · rejected.md · run-log.md

## Coverage
<coverage.py map output>

### Not checked in this run
- <item> — <why>

### Permanent gaps
- <item> — <why this setup cannot check it>

## Bugs (confirmed)
### 1. <title>
<platform> · <screen> · <severity> · confidence <c> · <rule> · <file>#<id>
1. <step>
2. <step>
**Expected:** … **Actual:** …
Evidence: <crop path> · found by <found_by>
Also: <title of a finding merged into it> (<file>#<id>)

## Still reproduces
- **<title>** (BUG-N), <platform>, <screen>, severity <s> · <crop path>

## Improvements: <new> new, <known> already in the backlog (imp.md)
| screen | new | already open | the new ones (imp.md numbers) |
|---|---|---|---|
| <screen> | <n> | IMP-N, … | 1. <title>; 2. <title>; … |

## Not verified
- <title> — <screen> — why the verifier could not decide

## Look with your own eyes (unsure)
### <class> (<n>) — <why, once for the class>
- <title> — <screen>, <severity> · <crop path> · <file>#<id>
- low: <file>#<id>, <file>#<id>, …

## Known candidates
- <screen>: <what the app does> — <file>: «<the comment>»

## Open spec conflicts
- <screen>: <rule id> — <what the frame shows> · <file>

## Questions
- **<screen>**:
  - <question> (<options>)
  - spec text: <rule> «<spec>» → «<app>», …

## Regression flows written
- <maestro_dir>/fresh-eyes-<id>.yaml
```

- Not checked in this run: the plan's `unchecked` items without `gap`, then each cell not done (`na`, `blocked`,
  `failed`) with its note, e.g. `android/flight-search/default/swipe-inside — not enough items` (a swipe that
  moved nothing is never done).
- A bug is at most 10 lines: at most 5 steps (join the way there into one, "Open Trips → Paris → Schedule"),
  expected and actual one sentence each; the backlog entry gets the full finding.
- Unsure, by class, never one block per finding, in this order and only classes that have findings: `stale`
  (the verdict's `stale`), `asked` (the verdict's `asked`; its question is under Questions), `intermittent`
  (`verify_note` "intermittent: …"), then the rest (what a person should look at). Lows share one line of ids.
- Questions: one bullet per screen, never one per finding, at most 10: the screen in bold alone, under it a
  sub-item per question with its options (its `questions[]`, its stale question), then one sub-item with its
  `spec-text` pairs. More screens: the 10 with the most severe findings, and one line counting the rest (they stay in the
  findings files). At most 10 question sub-items in all, the same way. `coverage.py score` counts the sub-items (M5); keep the marker `spec text:` as is in any
  language: that sub-item is no question.
- Open spec conflicts: the `conflict` entries, one line each, no question (the spec already asks the owner).
- Known candidates: the `known` entries, one line each, not counted as questions; what the owner accepts goes
  to `known.md`.

## Backlog templates

`backlog.py` templates: `ru-backlog` (Где / Шаги / Ожидание / Факт / Заметки) and `en-backlog`
(Where / Steps / Expected / Actual / Notes). New entries get the next free number and status `open`;
`quick BUG-N` rechecks set `verified` or back to `open` with a recheck line under the heading; a confirmed
finding with `backlog_id` gets a recheck line and status `open` instead of a new entry. `backlog.py index` lists
the entries whose heading status is `open`, `in-progress`, `planned` or empty, one line each, for the verifier.
