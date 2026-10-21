# Report, findings and backlog formats

## Run folder

```
.fresh-eyes/runs/<YYYYMMDD-HHMM>-<mode>/
  preflight.json  coverage.json  report.md
  <platform>/<screen>/<state>-<probe>/      ← one evidence pack per cell
    screen.png screen_1000.png som.png som.json nodes.json hierarchy.txt candidates.json meta.json
    crops/*.png  [video.mp4 motion.json sheet_*.png around.json]  [taps.json]
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
  "som": [3], "bbox": [0, 0, 0, 0], "expected": "", "actual": "", "steps": [""],
  "rule": "spec:<id> | taste", "evidence": ["crops/clipped-1.png"],
  "verdict": "pending|confirmed|unsure|rejected", "verify_note": "",
  "same_as": "<findings file relative to the run>#<id> (optional)"}],
 "questions": [{"text": "", "options": [""]}]}
```

- Text fields (`title`, `expected`, `actual`, `steps`, `verify_note`, `questions`) are in the config's
  `language`; ids, paths, rule ids and on-screen text stay as they are.
- `same_as`: the verifier marks a finding that reports the same defect as another one (split reviewers, a
  journey adding evidence). The verdict leaves it out of the counts only when the finding it points to is
  confirmed, of the same kind and not lower in severity, and it was itself ruled on (never while pending); it
then returns it in `merged`, and the report lists it under that finding.
- `reviewed_packs`: every pack whose `som.png` the reviewer actually opened, and only those. A done cell whose
  packs no reviewer listed keeps the verdict INCOMPLETE.
- `rule: taste` (no spec rule behind it) caps `confidence` at 0.6.
- The verifier sets `verdict` and `verify_note`; nothing reaches the backlog while `pending`.

## `requests.json` (verifier → navigator, in the screen folder)

`[{"finding": "f1", "pack": "<path>", "do": "resnap|repeat-probe|crop", "probe": "<id>", "bbox": [..]}]`; the
navigator adds `"done": "<path>"` or `"failed": "<why>"` to each entry.

## Verdict

Computed only by `coverage.py verdict` (never by a model):
- any confirmed BUG of severity high → `BLOCK` (НЕ ВЫПУСКАТЬ);
- confirmed medium with full coverage → `ISSUES` (ЕСТЬ ПРОБЛЕМЫ);
- any cell not done/na, any unchecked item, any done cell whose packs no reviewer listed, any finding still
  `pending`, any `unsure` BUG of severity high or medium (e.g. intermittent) → `INCOMPLETE`
  (НЕ ПРОВЕРЕНО ДО КОНЦА);
- otherwise `CLEAN` (ЧИСТО). IMP findings and low unsure ones never change the verdict.

## `report.md` (project language)

```markdown
# fresh-eyes — <mode> — <date>
**Verdict: <label>** — <reasons, one line each>
Build: <branch/commit>, backend: <api host or "not verified">, devices: <names>

## Coverage
<coverage.py map output>

### Not checked and why
- <item> — <why>

## Bugs (confirmed)
1. **<title>** — <platform>, <screen>, severity <s>, confidence <c>
   Steps: … Expected: … Actual: … Evidence: <crop path> Rule: <rule> Backlog: <BUG-N>

## Improvements
1. **<title>** — <what to change and why>, reference: <app/pattern>

## Not verified
- <finding still pending, and why the verifier could not decide>

## Look with your own eyes (unsure)
- <title> — <why unsure> — <crop path>

## Questions
- <spec conflicts and "bug or intended?" questions>

## Regression flows written
- <maestro_dir>/fresh-eyes-<id>.yaml
```

## Backlog templates

`backlog.py` templates: `ru-backlog` (Где / Шаги / Ожидание / Факт / Заметки) and `en-backlog`
(Where / Steps / Expected / Actual / Notes). New entries get the next free number and status `open`;
`quick BUG-N` rechecks set `verified` or back to `open` with a recheck line under the heading.
