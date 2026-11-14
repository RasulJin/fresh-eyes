---
name: fresh-eyes
description: Tests a running iOS/Android app like a picky QA engineer and designer (visual, motion, interaction, UX bugs) and writes them to the backlog. Run only when the user asks: quick ~$10–15, full: hours.
argument-hint: "init | quick [BUG-N | screens | what changed] [twice] | full [twice] [--resume] | bench"
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
5. Subagents get paths and return ≤10 lines. Keep screenshots and videos out of this conversation. Wait for
   every agent you start before the next step and never end your turn while one runs: a headless run
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
- Parse `$ARGUMENTS`: first word = subcommand (`init`, `quick`, `full`, `bench`); default `quick`.

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
   three keep their reports and the images findings link; the newest full run stays whole for `--resume`).
   Then `RUN=.fresh-eyes/runs/<YYYYMMDD-HHMM>-quick`
   (mkdir). If `.fresh-eyes/config.json` is missing → run `init`.
2. Make the build current: for each platform, if Metro is not running with the config's `metro_env`, start
   `builds.quick.<p>.start` in the background with that env; if the app is missing, run `builds.quick.<p>.build`
   (background; minutes).
3. `python3 $S/preflight.py --project . --mode quick --fix --brief [--source built|<ref>] --out $RUN/preflight.json`
   (`built` when step 2 built the app here; the commit the user names for an installed build made elsewhere;
   nothing when Metro serves it). Reviewers read code only from its `source.root`. If iOS reports
   "no Maestro iOS driver", call the Maestro MCP `inspect_screen` on that simulator once and run preflight
   again. If no platform is ready, or the computer's disk is nearly full (`disk_free_gb` under 15), stop: tell
   the user what is missing or how much is free; the verdict is INCOMPLETE.
   Then `python3 $S/spec.py stale --spec .fresh-eyes/spec.md --repo . --config .fresh-eyes/config.json --head
   <source.ref> --out $RUN/spec-stale.json`: screens whose code changed after the spec's `Updated` date (the
   verdict turns their `spec:` findings into questions; fix/docs/test/chore/style commits and commits naming a
   BUG-N do not count). `<source.ref>` is preflight.json's commit, so a checkout
   on another branch does not hide the build's changes; leave `--head` out when there is no commit there.
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
6. Smoke: navigator on the first cell of each ready platform. If it ends `blocked`, stop and report why.
   Any navigator reply `needs: ios-driver` (here, in step 7 or in requests mode): call the Maestro MCP
   `inspect_screen` on that simulator once, then call the navigator again with what it had left.
7. Navigation: one `fresh-eyes:navigator` per platform (they may run in parallel), ≤15 cells per call, until
   `coverage.py next` is empty. Prompt: run folder, config path, platform, device id and name from preflight,
   the cell ids, `package: <app.android_id>` (it goes to every `capture.py snap --package`, which saves the
   crash log when the app died), and `app language: <app.locale>` when config sets it. A navigator that finds
   the app signed out signs in with `accounts` itself; cells blocked with
   "signed out: <variable> not set" mean the user has to export that variable before the next run; cells
   blocked with "consent: <text>" need the user's yes for that dialog in config `consents` (ask after the
   report, never let an agent accept it). A reply line
   `reach: <screen>: <how>`: write `<how>` into that screen's `reach` in `.fresh-eyes/config.json`, so the next
   run does not hit the same wall. Liveness: every `capture.py` call and every `nav.py` call with `--run $RUN`
   refreshes `$RUN/<platform>/heartbeat`; `python3 $S/nav.py idle --run $RUN --platform <p>` prints its age. Only
   a navigator that has run 10 minutes with `idle_s` over 600 is stuck (a permission prompt nobody answers, a
   loop): stop it (TaskStop) and start a fresh one on the cells `coverage.py next` still lists. Never go by the
   mtime of the agent's output file (a symlink: the time it was made). Check every 1–2 minutes, not in one long
   wait: an agent that finished meanwhile is noticed only when the check returns.
8. Review: first, per screen with done cells, `python3 $S/spec.py slice --spec .fresh-eyes/spec.md --screens
   <id>,<its neighbors> --stale $RUN/spec-stale.json --out $RUN/spec/<id>.md` (the global rules and those
   sections; neighbors' rules often apply); journeys: `--screens` = the screens of the journeys walked, `--out
   $RUN/spec/journey.md`. Then per screen × platform with done cells `python3 $S/coverage.py packs --dir
   $RUN/<platform>/<screen>` and one `fresh-eyes:reviewer` per group it prints (up to 4 in parallel; two or more
   groups: each writes its own `findings-<k>.json` in the screen folder). Prompt: the group's snap folders, the
   screen id and its spec slice path (never `.fresh-eyes/spec.md`),
   `.fresh-eyes/known.md`, `.fresh-eyes/examples/`, the `references` and `language` from config, `source_root`
   (`source.root` from preflight.json, or `none`). Journey cells get one reviewer per platform over
   `<run>/<platform>/journey/`;
   its findings carry the real screen they concern.
9. Verify: once per run, `python3 $S/backlog.py index --file <backlog.path> --out $RUN/backlog-index.md` (open
   backlog entries, one line each; only verifiers get it, reviewers never see the backlog; no `backlog` in
   config: skip it). Then one
   `fresh-eyes:verifier` per screen folder that has pending findings (up to 4 in parallel); the prompt is
   `first pass`, that folder, its spec slice, `.fresh-eyes/known.md`, `$RUN/backlog-index.md`, `language`,
   `source_root`. Keep each verifier's agent id. The journey
   folder's verifier also gets the findings files of the screens its findings concern (to mark duplicates). Folders whose verifier
   answered `needs: requests`: first, each `"do": "redo-cell"` entry → `python3 $S/coverage.py set --cov
   $RUN/coverage.json --cell <cell> --status todo --note "<why>"`, that cell navigated again (step 7) and its new
   pack reviewed (step 8, a new `findings-<k>.json`), then the entry gets `"done": "<pack>"`; then one navigator per platform in requests mode with their `requests.json` paths;
   when every entry has `done` or `failed`, send `second pass` with SendMessage to that folder's first-pass
   verifier (it keeps what it already read). A fresh verifier with `second pass` and the same inputs only when
   that agent is gone.
10. `python3 $S/coverage.py verdict --cov $RUN/coverage.json --findings <all findings*.json> --language <config>`
    and `python3 $S/coverage.py map --cov $RUN/coverage.json --language <config>`. If the verdict names done
    cells no reviewer looked at, send those packs to a reviewer (`findings-<k>.json`), verify, and rerun it.
    Then `python3 $S/backlog.py match --index $RUN/backlog-index.md --findings <all findings*.json> --out
    $RUN/imp.md --language <config>`: an IMP an open backlog entry already proposes gets its `backlog_id`, and
    imp.md holds every IMP in full (the new ones numbered, the known ones a line each).
11. Write `$RUN/report.md`, `rejected.md` and `run-log.md` by `$R/report-format.md` (see "Report"; `imp.md` is
    step 10's). Then `python3 $S/coverage.py score --run $RUN --session "${CLAUDE_SESSION_ID}"`: the run's score
    card (`$RUN/score.json`, the metrics a script counts; the judge fills M4, M7 and M11 and may correct M1, a
    rerun keeps them).
12. Review with the user, then write the backlog and regression flows (see "After the report").

## full

Goal: before a release, everything from a fresh install. Same as quick, with these differences:
- `--mode full`: release builds from `builds.full`; preflight checks the API inside the installed app's bundle
  and gets `--source built` after that build (or the commit the user names when told to test what is installed).
- Screens: all of `config.screens`; also walk every `journeys[]` entry end to end (mutating journeys only
  when preflight allowed mutation).
- Fresh install on the owned devices: `devices.py app --do clear` (Android wipes the data; iOS uninstalls, so
  follow with `--do install --artifact <builds.full.ios.artifact>`), then `--do locale --locale <app.locale>` when
  config sets it, then launch. Sign-in: the navigator types
  `nav.py type --env <accounts.<role>.email_env>` and `--env <…password_env>` (the values live in the user's
  environment, never in prompts or files). No `accounts` in config → keep the installed state instead of
  clearing, and say so in the report.
- The navigator gets one journey or one screen per call.
- `full --resume`: take the newest `.fresh-eyes/runs/*-full/`, re-run preflight into it, and continue with
  `coverage.py next` until no todo cells remain. Never restart finished cells.

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
   the backlog, since run folders are git-ignored, and links it). A real bug with `backlog_id` gets no new entry (an improvement with one neither):
   `python3 $S/backlog.py set-status --file <backlog.path> --id <backlog_id> --status open --note "<date>,
   <device>, <build>: still reproduces, <evidence>"`. For `quick BUG-N`: the same `set-status` with `--status
   verified|open` and `<result>` in the note.
3. Intended bugs and declined improvements → append to `.fresh-eyes/known.md` (never raised again); record the
   intended bugs as "rejected by user" counts in `report.md` (this is the false-positive measure). Known
   candidates the user accepts go there too; a `spec-text` pair they accept is a spec update (step 5), one they
   turn down (the app is wrong) a new bug: write it as a finding for step 2.
4. For each new real BUG write a regression flow `<maestro_dir>/fresh-eyes-<BUG-id>.yaml` (tag `fresh-eyes`): reach
   the state, run the probe, assert the expected state (`assertVisible` / `assertNotVisible` /
   `extendedWaitUntil`). Visual-only bugs get a comment that `quick` re-checks them.
5. Propose a spec rule for bugs that revealed missing intent; write it only after the user agrees.
6. Final message: verdict, counts, the report path, open questions. Nothing else.
