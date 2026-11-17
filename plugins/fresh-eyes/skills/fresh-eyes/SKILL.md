---
name: fresh-eyes
description: Tests a running iOS/Android app like a picky QA engineer and designer (visual, motion, interaction, UX bugs) and writes them to the backlog. Run only when the user asks: quick ~$10–15, full: hours.
argument-hint: "init | quick [BUG-N | screens | what changed] [twice] | full [twice] | resume [<run>] | bench [seeded]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*)
---

# fresh-eyes

You orchestrate a test run of a mobile app the way a sharp human tester would use it: reach every screen in
realistic states, poke it with "evil" probes (rapid taps, tap outside, back mid-animation, keyboard, background),
record transitions, and judge everything against the project's own spec. You stay in this conversation;
the heavy work runs in subagents that exchange files with you.

## Hard rules

1. Never say CLEAN / ЧИСТО / "all good" unless `coverage.py verdict` returned code `CLEAN`. Report its verdict
   and reasons verbatim. Not checked ≠ fine: always list what was not checked.
2. Mutating probes (create, edit, delete, send) run only from cells that `coverage.py plan` put into
   `coverage.json`, which happens only when `preflight.json` verified a staging/local backend. Never improvise one.
3. Only change devices whose name starts with `fresh-eyes`. The user's own emulators and phones are read-only.
4. Never run the Maestro CLI on a device the Maestro MCP is driving (it kills the MCP session).
5. Subagents get paths and return ≤10 lines; their details go to `<run>/notes/` (`coverage.py notes` gathers
   them), never into this conversation, and so do screenshots and videos. Wait for every agent you start before
   the next step (the pipeline of quick step 8 excepted) and never end your turn while one runs: a headless run
   (`claude -p`) stops background agents 10 minutes after the turn ends.
6. Never write secrets (passwords, tokens, test credentials) into files, prompts or reports.
7. Questions to the user come from you (subagents cannot ask): spec conflicts, "bug or intended?", go-aheads.
8. You orchestrate, you do not test or judge. Run only the steps below: no reading the app's source code, no
   extra agents for a lead you find interesting, no ruling on findings yourself. A doubt about a finding goes
   to the verifier or into the report's questions. Every extra turn of yours re-reads this whole conversation.

## Setup

- `S=${CLAUDE_SKILL_DIR}/scripts`, `R=${CLAUDE_SKILL_DIR}/references`; run scripts as `python3 $S/<name>.py`.
- Project files live in `.fresh-eyes/` at the repo root: `config.json`, `spec.md`, `known.md`, `examples/`
  (committed) and `runs/`, `cache/` (git-ignored).
- Agents: `fresh-eyes:navigator`, `fresh-eyes:reviewer`, `fresh-eyes:verifier`. Call them by these full names
  with the Agent tool and never pass `model` (it would override the agent's own model). Never stand in a
  general-purpose agent with their text: it starts with every MCP tool name and skill of the session (~78k
  tokens instead of ~7k) and repeats that on every turn. No such agents → the plugin is not loaded; stop.
- Parse `$ARGUMENTS`: first word = subcommand (`init`, `quick`, `full`, `resume`, `bench`); default `quick`.

## init

Goal: a working `.fresh-eyes/` for this project, confirmed by the user.

1. Look at the project: framework and router (Expo Router `app/`), bundle ids (`app.json`/`app.config.*`),
   build profiles and API URLs (`eas.json`, `.env*` — read URLs only), the Maestro folder, the backlog file,
   design sources (design files, screenshots, theme/token packages, motion constants), decision logs.
2. Write `.fresh-eyes/config.json` by `$R/config-format.md`:
   - `env.api_expected` = the staging/local URL, `api_forbidden` = production URLs. If you cannot find a
     staging backend, set `env.name: "prod"` (mutation stays off) and tell the user.
   - `screens[]` from route files (skip `_layout`, `+not-found`, redirects). Default states
     `["default", "dark", "font-xl", "small-screen"]`, plus `keyboard` for screens with inputs. Probes by kind:
     lists/grids → `swipe-inside`, `tap-everything`; forms → `keyboard-open`, `hardware-back-keyboard`;
     search/autocomplete fields with a read-only button (search, filter) → `search`;
     screens where a person does something (forms, search, chat, create/edit) → `scenario`, with `task` and
     `forms`;
     overlays/maps/menus → `tap-outside`, `back-during-animation`; share/send buttons → `rapid-tap`;
     drawers, pagers, draggable sheets → `drag-interrupt`, with `draggable`; a page of a pager or tab set
     also gets `pages` (the other tabs) for `swipe-inside`;
     long requests → `background-foreground`. Fill `reach` in plain words, `neighbors`, and `marker` (header
     title or a testID from the route file; another screen must not show it). When the app has its own theme
     setting, fill `app_theme` and add `theme-mismatch` after `dark` on screens with maps, web views or charts.
3. Build `.fresh-eyes/spec.md` by `$R/spec-format.md`. Delegate the reading to a general-purpose subagent
   (give it the source paths and the format file; it returns the path and a list of conflicts).
4. `.fresh-eyes/known.md` (empty template), `.fresh-eyes/examples/`: copy screenshots of past bugs from the
   backlog's images folder with a short `README.md` index (id, one line, file) — skip ids listed in
   `.fresh-eyes/bench/cases.json` if it exists. Add `.fresh-eyes/runs/`, `.fresh-eyes/cache/`,
   `.fresh-eyes/bench/runs/` to `.gitignore`.
5. Devices: `python3 $S/devices.py ensure --platform <p>` for each platform in config (owned devices only).
6. Show the user the allow-rules a long run needs (they add them to `.claude/settings.local.json`; you do not):
   `Bash(python3 <this skill dir>/scripts/*)`, `Bash(adb:*)`, `Bash(xcrun simctl:*)`, `mcp__<maestro server>__*`.
7. Ask the user to confirm: the env URLs, the screen list, every `⚠ conflict` in the spec, the 5–10 most
   important spec rules, and which consent dialogs the test accounts may accept (terms, community rules, an AI
   notice: its text and button, into `consents`; none named → none accepted). Write their answers into the files.

## quick

Goal: after a fix or a feature, check what it touched and what it could break. Args: `BUG-N`, screen ids, or
words / nothing (= the current diff).

1. Slim older runs first: `python3 $S/coverage.py prune --runs .fresh-eyes/runs --keep 3` (runs before the last
   three keep their reports and the images findings link; the newest full run stays whole for `resume`).
   Then `RUN=.fresh-eyes/runs/<YYYYMMDD-HHMM>-quick` (mkdir). If `.fresh-eyes/config.json` is missing → `init`.
2. Make the build current: for each platform, if Metro is not running with the config's `metro_env`, start
   `builds.quick.<p>.start` in the background with that env; if the app is missing, run `builds.quick.<p>.build`
   (background; minutes).
3. `python3 $S/preflight.py --project . --mode quick --fix --brief [--source built|<ref>] --out $RUN/preflight.json`
   (`built` when step 2 built the app here; the commit the user names for an installed build made elsewhere;
   nothing when Metro serves it). Add `--apk <path>` when the build to test is not at
   `builds.<mode>.android.artifact` (made elsewhere, downloaded): `--fix` installs it on both owned devices;
   never `adb install` by hand. Without `--apk` or `--source built` the artifact goes only where no app is;
   `installed` in preflight.json lists what went where. Reviewers read code only from its `source.root`. If iOS reports
   "no Maestro iOS driver", call the Maestro MCP `inspect_screen` on that simulator once and run preflight
   again. If no platform is ready, or the computer's disk is nearly full (`disk_free_gb` under 15), stop: tell
   the user what is missing or how much is free; the verdict is INCOMPLETE.
   Then `python3 $S/spec.py stale --spec .fresh-eyes/spec.md --repo . --config .fresh-eyes/config.json --head
   <source.ref> --out $RUN/spec-stale.json`: screens whose code changed after the spec's `Updated` date (the
   verdict turns their `spec:` findings into questions; fix/docs/test/chore/style commits and commits naming a
   BUG-N do not count). `<source.ref>` is preflight.json's commit, so a checkout on another branch does not hide
   the build's changes; leave `--head` out when there is no commit there.
4. Screens:
   - `BUG-N`: read the entry in the backlog (heading + its fields); its replay becomes the coverage cell
     `<platform>/recheck-BUG-N/default/replay` (step 5, `--recheck BUG-N`), reviewed against its "Expected"
     field. Then take screens from the fix: `git log --format=%H -1 -E --grep 'BUG-N([^0-9]|$)'` (so BUG-1
     does not match BUG-12), and `python3 $S/coverage.py diff-screens --repo . --base <that commit>^ --config
     .fresh-eyes/config.json`. No such commit → no diff screens; say so.
   - screen ids: use them. Otherwise: `diff-screens --base <merge-base with the default branch>` (most-hit
     screens first); list `unmapped` files in the report as areas without a screen.
   - Quick checks at most 4 screens and no neighbors; the plan lists the rest as not checked, so such a run
     ends INCOMPLETE, never CLEAN. Pass `--max-screens <n>` only when the user named more screens themselves.
   - No screens and no `BUG-N` (only unmapped files changed): do not plan. List the unmapped files and ask the
     user for screen ids or suggest `full`.
5. `python3 $S/coverage.py plan --config .fresh-eyes/config.json --preflight $RUN/preflight.json --mode quick
   --screens a,b [--recheck BUG-N] [--max-screens N] --out $RUN/coverage.json`. Tell the user the cell count and the unchecked
   items in one line. If the plan has seeded states, run `hooks.seed` now (the plan only has them when
   preflight allowed mutation) and `hooks.cleanup` after the run.
6. Smoke: navigator on the first cell of each ready platform. If it ends `blocked` or with "app crashed" (it
   signed in first: a crash after sign-in shows here), stop and report why with its pack's `logcat.txt`.
   Any navigator reply `needs: ios-driver` (here, in step 7 or in requests mode): call the Maestro MCP
   `inspect_screen` on that simulator once, then call the navigator again with what it had left.
7. Navigation: per platform two `fresh-eyes:navigator`s at once, one per queue, ≤15 cells per call, until
   `coverage.py next --cov $RUN/coverage.json --queue main|small` is empty: `main` on preflight's `device`,
   `small` on its `small_device_id` (none: the small queue after main, on `device`). Prompt: run folder, config
   path, platform, `queue: <main|small>`, that device's id and name, the cell ids, `package: <app.android_id>`
   (`capture.py snap --package` keeps the crash log), and `app language: <app.locale>` when config sets it. A
   navigator that finds the app signed out signs in with `accounts` itself; cells blocked with "signed out:
   <variable> not set" mean the user has to export that variable or add its keychain item (`$R/config-format.md`)
   before the next run; cells blocked with "consent: <text>" need the user's yes for that dialog in config
   `consents` (ask after the report, never let an agent accept it). A reply line `reach: <screen>: <how>`: write
   `<how>` into that screen's `reach` in `.fresh-eyes/config.json`. Liveness: every `capture.py`, `taps.py` and
   `nav.py` call with `--run $RUN --queue <q>` refreshes `$RUN/<platform>/heartbeat-<q>`. Right after starting
   navigators, wait in one blocking call each, in one message (Bash timeout 600000), never by polling: `python3
   $S/nav.py idle --run $RUN --platform <p> --until 540 --queue <q> --cells <its cell ids>` (requests mode:
   `--requests <its requests.json paths>`). `"state"`: `done` = its cells are recorded; `running` = call again;
   `stuck` = no heartbeat for 9 minutes (a permission prompt nobody answers, a loop): stop it (TaskStop) and start
   a fresh one on the cells `coverage.py next` still lists. Never go by the mtime of the agent's output file (a
   symlink).
8. Pipeline: as soon as a screen's cells on a platform are recorded (`idle` said `done`, or its navigator
   replied), run this step and step 9's first pass for that screen while the navigators work, in two messages:
   one Bash call chaining its `spec.py slice` and `coverage.py packs`, then all its reviewers (and the next
   navigator) at once. Requests mode and second passes wait until all navigation is done.
   Review: first, per screen with done cells, `python3 $S/spec.py slice --spec .fresh-eyes/spec.md --screens
   <id>,<its neighbors> --stale $RUN/spec-stale.json --out $RUN/spec/<id>.md` (the global rules and those
   sections; neighbors' rules often apply); journeys: `--screens` = the screens of the journeys walked, `--out
   $RUN/spec/journey.md`. Then per screen × platform with done cells `python3 $S/coverage.py packs --dir
   $RUN/<platform>/<screen>` and one `fresh-eyes:reviewer` per group it prints (up to 4 in parallel; two or more
   groups: each writes its own `findings-<k>.json` in the screen folder). Prompt: the group's snap folders, the
   screen id and its spec slice path (never `.fresh-eyes/spec.md`), `.fresh-eyes/known.md`,
   `.fresh-eyes/known-proposed.md`, `.fresh-eyes/examples/`, the `references` and `language` from config,
   `source_root` (`source.root` from preflight.json, or `none`). Journey cells get one reviewer per platform over
   `<run>/<platform>/journey/`; its findings carry the real screen they concern.
9. Verify: once per run, `python3 $S/backlog.py index --file <backlog.path> --ref <source.ref> --config
   .fresh-eyes/config.json --out $RUN/backlog-index.md` (open backlog entries, one line each, as of the build's
   commit: a checkout on another branch has another backlog; no commit in preflight.json: no `--ref`; only
   verifiers get it, reviewers never see the backlog; no `backlog` in config: skip it), then `python3
   $S/backlog.py match --index $RUN/backlog-index.md --findings <the findings*.json going to a verifier now>` (a
   pending BUG gets the open BUG entry it may repeat, for the verifier to check; never a file a verifier holds) and
   `python3 $S/findings.py same --run $RUN --findings <the same files>` (one case on several screens is checked
   once: the others get `same_as` to it and later its ruling). Then one `fresh-eyes:verifier` per screen folder
   with a file in its `verify` list (up to 4 in parallel; IMPs and linked copies alone need none); the prompt is
   `first pass`, that folder, its spec slice, `.fresh-eyes/known.md`, `.fresh-eyes/known-proposed.md`,
   `$RUN/backlog-index.md`, `language`, `source_root`, and never the backlog file's path. Keep each verifier's
   agent id. The journey folder's verifier also gets the findings files of the screens its findings concern (to
   mark duplicates). Folders whose verifier answered `needs: requests`: first, each `"do": "redo-cell"` entry →
   `python3 $S/coverage.py set --cov $RUN/coverage.json --cell <cell> --status todo --note "<why>"`, that cell
   navigated again (step 7) and its new pack reviewed (step 8, a new `findings-<k>.json`), then the entry gets
   `"done": "<pack>"`; then `python3 $S/findings.py requests --run $RUN` (merges an entry asked twice) and
   navigators in requests mode, ≤15 open entries per call, packs shot on the small device to its navigator, both
   devices at once; when every entry has `done` or `failed`, send `second pass` with SendMessage to that folder's
   first-pass verifier (it keeps what it already read; a fresh verifier with `second pass` and the same inputs
   only when that agent is gone). A second pass that answers `needs: requests` again (a medium with one repeat
   back) gets one more requests round and `second pass`; what is still pending after it stays pending.
10. `python3 $S/findings.py same --run $RUN --findings <all findings*.json>` (the linked copies take their case's
    ruling; its `links`, every [copy, target], go to run-log.md: no verifier sees a copy), then
    `python3 $S/coverage.py verdict --cov $RUN/coverage.json --findings <all findings*.json> --language <config>`
    and `python3 $S/coverage.py map --cov $RUN/coverage.json --language <config>`. If the verdict names done
    cells no reviewer looked at, send those packs to a reviewer (`findings-<k>.json`), verify, and rerun it.
    Code `CONFLICT` (one defect ruled both ways): each pair of its `conflicts` goes to one verifier, `second pass`
    with both findings files and the pair (the first file's verifier by SendMessage, else a fresh one); when all
    are back, run `findings.py same` and the verdict again. One round only: a pair still in `conflicts` → in both
    findings files set both `"verdict": "unsure"`, `verify_note` "rulings disagree: owner decides (<other
    file#id>)", then `findings.py same` and the verdict once more, and write the report (the pair is unsure).
    Then `python3 $S/backlog.py match --index $RUN/backlog-index.md --findings <all findings*.json> --out
    $RUN/imp.md --language <config>`: an IMP an open backlog entry already proposes gets its `backlog_id`, and
    imp.md holds every IMP in full (the new ones numbered, the known ones a line each). Then `python3
    $S/findings.py known --run $RUN --out .fresh-eyes/known-proposed.md`: the verifiers' known candidates and test
    data, which the next run's reviewers skip.
11. Write `$RUN/report.md`, `rejected.md` and `run-log.md` by `$R/report-format.md` (see "Report"; `imp.md` is
    step 10's). Then `python3 $S/coverage.py notes --run $RUN` (the agents' notes into `orchestrator-notes.md`)
    and `python3 $S/coverage.py score --run $RUN --session "<RESUME.md's sessions>,${CLAUDE_SESSION_ID}"` (every
    session of the run; no RESUME.md: this one): the run's score card (`$RUN/score.json`, the metrics a script
    counts; the judge fills M4, M7 and M11, may correct M1; a rerun keeps them).
12. Review with the user, then write the backlog and regression flows (see "After the report").

## full

Goal: before a release, everything from a fresh install. Same as quick, with these differences:
- `--mode full`: release builds from `builds.full`; preflight checks the API inside the installed app's bundle
  and gets `--source built` after that build (or the commit the user names when told to test what is installed).
- Screens: all of `config.screens`; also walk every `journeys[]` entry end to end (mutating journeys only
  when preflight allowed mutation).
- Fresh install on the owned devices: `devices.py app --do clear` (Android wipes the data; iOS uninstalls, so
  follow with `--do install --artifact <builds.full.ios.artifact>`), then `--do locale --locale <app.locale>` when
  config sets it, then launch. Sign-in: the navigator types `nav.py type --env <accounts.<role>.email_env>
  --keychain fresh-eyes-<role>-email`, the same for the password (the values live in the user's environment or
  keychain, never in prompts or files). No `accounts` in config → keep the installed state, and say so.
- A `main` navigator gets one journey or one screen per call; the `small` queue goes to one call, all its cells.
- Before step 6: `bench seeded` (it needs no device) with `--run $RUN` on its `bench.py score`, which writes
  `bench` into score.json; fewer than 4 of 5 caught → stop and tell the user (verdict INCOMPLETE).

## Checkpoints and resume

Checkpoints: after navigation (`coverage.py next` empty on every queue) and after every first verifier pass, once
each agent you started is back, write `$RUN/RESUME.md`: the step reached; what is left (todo cells, packs no
reviewer listed, folders without a first pass, open requests); the verifiers' agent ids; steps taken by hand;
`sessions: <ids>` (every session of the run, this one last). Then go on: a run nobody watches must reach its report.
After an interruption (a crash, a closed session) the user runs `/fresh-eyes resume <run>` in a new session. `resume
[<run>]` (default: the newest run; `full --resume`: the newest full one): read its `RESUME.md`, add
`${CLAUDE_SESSION_ID}` to its `sessions:`, run step 3 into it again (same mode and `--source`), then go on from the
first unfinished step (a second pass goes to a fresh verifier when the first one is gone). Never redo a done cell.

## twice

`quick … twice` or `full twice` (before a release): two independent passes over the same cells, findings
merged; one pass catches about three quarters of what two catch. Same steps, with these differences:
- `coverage.py plan … --passes 2`: every cell gets a twin `<id>#2` (a backlog replay runs once).
- Navigation: all first-pass cells, then the `#2` cells with fresh navigators (their packs end in `-p2`).
- Review: `coverage.py packs` never mixes the passes; a group of `-p2` packs goes to a second-pass reviewer,
  which writes `findings-p2.json` (or `findings-p2-<k>.json`) and never sees first-pass findings.
- Verify: one verifier per screen folder over both passes' findings; a defect both passes found gets `same_as`,
  so the verdict and the report count it once.

## bench

Measures whether the skill catches known bugs (`.fresh-eyes/bench/cases.json`; none → say so and stop). Code finds
the candidates, a model judges them; keywords never decide.
1. Attempts: three ordinary `quick` runs over the cases' screens (`--max-screens` = their number) on a build where
   the bugs live, each in its own run folder. Pairs: one more run on a build with the fixes.
2. `python3 $S/bench.py sheet --cases .fresh-eyes/bench/cases.json --runs <attempt runs> --fix-runs <ID=fix run>…
   --out .fresh-eyes/bench/sheet.json`: per case and run, the findings on the case's screen and area.
3. One `fresh-eyes:judge` with the sheet path: it marks which finding, if any, is each case's defect.
4. `python3 $S/bench.py score --cases .fresh-eyes/bench/cases.json --sheet .fresh-eyes/bench/sheet.json --out
   .fresh-eyes/bench/score.json`: caught = matched in at least 2 of 3 attempts; a match on the fix build is a false
   positive; a CLEAN run that missed a planned case is a false CLEAN. Report these against the spec's targets.
   Cases with `"kind": "control"` are intended behaviour on the same screens (`control` in the score): any
   match on them is a false positive too.

`bench seeded`: the human classes on fixtures, no device (an overlap by a pinned bar, arithmetic, a label cut at
the largest font, a stale screen-reader name after a tap, a field jumping twice with the keyboard, and a control:
a tap that landed on its neighbour). Three times `python3 $S/bench.py seed --out .fresh-eyes/bench/runs/seeded-<k>`
and in each run steps 8 and 9 with the slices already in `$RUN/spec/` (no `spec.py slice`), no `known.md`,
`known-proposed.md` or backlog index, `source_root: none`, `language: en`; a request there cannot be fetched:
mark it `"failed": "seeded bench, no device"`, then the second pass.
Then steps 2-4 with `--cases $R/bench/cases.json`, the three runs and no fix runs; 4 of 5 caught is the bar.

## Report

`report.md` (at most 300 lines) and its companions by `$R/report-format.md`, in the project language: the verdict
line and reasons verbatim from `coverage.py verdict`, build/backend/devices, the coverage map, "not checked in
this run" (preflight `unchecked`, plan `unchecked` items without `gap`, blocked cells with notes, unmapped files)
and apart from it "permanent gaps" (plan items with `"gap": true` and platform gaps from `$R/platforms.md`: this
setup can never check them, they do not block the verdict). A finding the verdict lists in `merged` goes under
the one its `same_as` points to, or the confirmed one with the same `log` line; a confirmed bug with
`backlog_id` goes under "still reproduces (BUG-N)"; improvements are only the new ones of imp.md, a row per
screen with their imp.md numbers and titles; the findings the verdict lists in `asked` and `stale` go under
"look with your own eyes", never under bugs or into the backlog. Every step you took by hand outside these
steps (a resnap, a config fix, a stopped agent) goes to `run-log.md`. Over 300 lines: shorten items, never drop
a confirmed bug or a question.

Then stop the devices this run booted (`ensure` printed `"booted": true`), `python3 $S/devices.py stop --platform
<p>`: an emulator holds ~2.5 GB of RAM. One that was already running stays up: it boots without saving state, so a
stop throws away a build or a sign-in someone put on it.

## After the report

1. Show the user a numbered list of confirmed bugs (new and "still reproduces": title, screen, platform,
   severity, crop path), then the new improvements by screen (their imp.md numbers and titles) and the path of
   `imp.md`. Ask which bugs are real or intended and which improvements (by their imp.md numbers) to take.
   Default if they just say ok: all confirmed bugs are real and every improvement goes in as a proposal.
2. New real bugs and taken improvements → backlog: write the finding to `$RUN/backlog-<k>.json`, then `python3
   $S/backlog.py append --file <backlog.path> --finding $RUN/backlog-<k>.json --template <template> --attach
   $RUN/<its main crop> --images-dir <backlog.images_dir>` (assigns the BUG or IMP id, copies the crop next to
   the backlog, since run folders are git-ignored, and links it). A real bug with `backlog_id` gets no new entry
   (an improvement with one neither; an id from `backlog.extra` is only named in the report); the others get
   `python3 $S/backlog.py set-status --file <backlog.path> --id <backlog_id> --status open --note "<date>,
   <device>, <build>: still reproduces, <evidence>"`. For `quick BUG-N`: the same `set-status` with `--status
   verified|open` and `<result>` in the note.
3. Intended bugs and declined improvements → append to `.fresh-eyes/known.md` (never raised again); record the
   intended bugs as "rejected by user" counts in `report.md` (this is the false-positive measure). Each line
   of `known-proposed.md` the user rules on leaves it: accepted → moved to `known.md`; declined (a real bug) →
   deleted, so reviewers flag it again; only lines not yet ruled stay. A `spec-text` pair they accept is a spec
   update (step 5), one they turn down (the app is wrong) a new bug: write it as a finding for step 2.
4. For each new real BUG write a regression flow `<maestro_dir>/fresh-eyes-<BUG-id>.yaml` (tag `fresh-eyes`): reach
   the state, run the probe, assert the expected state (`assertVisible` / `assertNotVisible` /
   `extendedWaitUntil`). Visual-only bugs get a comment that `quick` re-checks them.
5. Propose a spec rule for bugs that revealed missing intent; write it only after the user agrees.
6. Final message: verdict, counts, the report path, open questions. Nothing else.
