# Report, findings and backlog formats

## Run folder

```
.fresh-eyes/runs/<YYYYMMDD-HHMM>-<mode>/
  preflight.json  coverage.json  spec-stale.json  backlog-index.md  report.md
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
  "same_as": "<findings file relative to the run>#<id> (optional)", "backlog_id": "BUG-N (optional)"}],
 "questions": [{"text": "", "options": [""], "finding": "<id> | <findings file relative to the run>#<id>"}]}
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
- `reviewed_packs`: every pack whose evidence the reviewer actually opened (its `som.png`, or its crops plus
  `candidates.json`), and only those. A done cell whose packs no reviewer listed keeps the verdict INCOMPLETE.
- `backlog_id`: the verifier sets it on a confirmed BUG that an open entry of `backlog-index.md` already
  describes, with `verify_note` "already open: BUG-N". It still counts as a confirmed bug; the report lists it
  under "still reproduces" and the backlog gets a recheck line instead of a new entry.
- `rule: taste` (no spec rule behind it) caps `confidence` at 0.6.
- The verifier sets `verdict` and `verify_note`. No BUG reaches the backlog while `pending`; IMPs are proposals
  and go in when the owner picks them. The verifier only rejects an IMP the spec slice or `known.md`
  contradicts; a `pending` IMP is final.

## `requests.json` (verifier → navigator, in the screen folder)

`[{"finding": "f1", "pack": "<path>", "do": "resnap|repeat-probe|crop", "probe": "<id>", "bbox": [..]}]`; the
navigator adds `"done": "<path>"` or `"failed": "<why>"` to each entry.

## Verdict

Computed only by `coverage.py verdict` (never by a model); the first that applies wins:
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
Write one question per stale screen from the verdict's reason line (section ids and the `Updated` date):
"section <id> dated <Updated>, code changed since: are its rules current?".

## `report.md` (project language)

```markdown
# fresh-eyes — <mode> — <date>
**Verdict: <label>** — <reasons, one line each>
Build: <branch/commit>, backend: <api host or "not verified">, devices: <names>

## Coverage
<coverage.py map output>

### Not checked in this run
- <item> — <why>

### Permanent gaps
- <item> — <why this setup cannot check it>

## Bugs (confirmed)
### 1. <title>
<platform>, <screen>, severity <s>, confidence <c>, rule <rule>
**Steps**
1. <step>
2. <step>
**Expected:** … **Actual:** …
Evidence: <crop path> · found by <found_by> · backlog <BUG-N>

## Still reproduces
- **<title>** (BUG-N), <platform>, <screen>, severity <s> · evidence <crop path>

## Improvements
1. **<title>** — <what to change and why>, reference: <app/pattern>

## Not verified
- <finding still pending, and why the verifier could not decide>

## Look with your own eyes (unsure)
- <title> — <why unsure> — <crop path>
- <a finding the verdict lists in `stale`> — stale spec — <crop path>
- <one question per stale screen, from the verdict's reason line>

## Questions
- <spec conflicts and "bug or intended?" questions>

## Regression flows written
- <maestro_dir>/fresh-eyes-<id>.yaml
```

## Backlog templates

`backlog.py` templates: `ru-backlog` (Где / Шаги / Ожидание / Факт / Заметки) and `en-backlog`
(Where / Steps / Expected / Actual / Notes). New entries get the next free number and status `open`;
`quick BUG-N` rechecks set `verified` or back to `open` with a recheck line under the heading; a confirmed
finding with `backlog_id` gets a recheck line and status `open` instead of a new entry. `backlog.py index` lists
the entries whose heading status is `open`, `in-progress`, `planned` or empty, one line each, for the verifier.
