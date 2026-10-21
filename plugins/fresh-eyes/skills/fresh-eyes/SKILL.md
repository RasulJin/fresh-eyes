---
name: fresh-eyes
description: Tests a running iOS/Android app like a picky human QA engineer and designer — visual, motion, interaction and UX bugs — and writes them to the backlog.
disable-model-invocation: true
argument-hint: "init | quick [BUG-N | screens | what changed] | full [--resume] | bench"
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
5. Subagents get paths and return ≤10 lines. Keep screenshots and videos out of this conversation.
6. Never write secrets (passwords, tokens, test credentials) into files, prompts or reports.
7. Questions to the user come from you (subagents cannot ask): spec conflicts, "bug or intended?", go-aheads.

## Setup

- `S=${CLAUDE_SKILL_DIR}/scripts`, `R=${CLAUDE_SKILL_DIR}/references`; run scripts as `python3 $S/<name>.py`.
- Project files live in `.fresh-eyes/` at the repo root: `config.json`, `spec.md`, `known.md`, `examples/`
  (committed) and `runs/`, `cache/` (git-ignored).
- Agents: `fresh-eyes:navigator`, `fresh-eyes:reviewer`, `fresh-eyes:verifier`. Call them by these full names
  with the Agent tool and never pass `model` (it would override the agent's own model).
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
     overlays/maps/menus → `tap-outside`, `back-during-animation`; share/send buttons → `rapid-tap`;
     long requests → `background-foreground`. Fill `reach` in plain words and `neighbors`.
3. Build `.fresh-eyes/spec.md` by `$R/spec-format.md`. Delegate the reading to a general-purpose subagent
   (give it the source paths and the format file; it returns the path and a list of conflicts).
4. `.fresh-eyes/known.md` (empty template), `.fresh-eyes/examples/`: copy screenshots of past bugs from the
   backlog's images folder with a short `README.md` index (id, one line, file) — skip ids listed in
   `.fresh-eyes/bench/cases.json` if it exists. Add `.fresh-eyes/runs/`, `.fresh-eyes/cache/`,
   `.fresh-eyes/bench/runs/` to `.gitignore`.
5. Devices: `python3 $S/devices.py ensure --platform <p>` for each platform in config (owned devices only).
6. Show the user the allow-rules a long run needs (they add them to `.claude/settings.local.json`; you do not):
   `Bash(python3 <this skill dir>/scripts/*)`, `Bash(adb:*)`, `Bash(xcrun simctl:*)`, `mcp__<maestro server>__*`.
7. Ask the user to confirm: the env URLs, the screen list, every `⚠ conflict` in the spec, and the 5–10 most
   important spec rules. Write their answers into the files.

## quick

Goal: after a fix or a feature, check what it touched and what it could break. Args: `BUG-N`, screen ids, or
words / nothing (= the current diff).

1. `RUN=.fresh-eyes/runs/<YYYYMMDD-HHMM>-quick` (mkdir). If `.fresh-eyes/config.json` is missing → run `init`.
2. Make the build current: for each platform, if Metro is not running with the config's `metro_env`, start
   `builds.quick.<p>.start` in the background with that env; if the app is missing, run `builds.quick.<p>.build`
   (background; minutes).
3. `python3 $S/preflight.py --project . --mode quick --fix --brief --out $RUN/preflight.json`. If iOS reports
   "no Maestro iOS driver", call the Maestro MCP `inspect_screen` on that simulator once and run preflight
   again. If no platform is ready, stop: tell the user what is missing; the verdict is INCOMPLETE.
4. Screens:
   - `BUG-N`: read the entry in the backlog (heading + its fields). Ask the navigator to replay its steps into
     `$RUN/<platform>/recheck-BUG-N/` (on the entry's platform, or both if none) and the reviewer to compare
     with its "Expected" field. Then take screens from the fix: `git log --format=%H -1 --grep BUG-N`, and
     `python3 $S/coverage.py diff-screens --repo . --base <that commit>^ --config .fresh-eyes/config.json`.
   - screen ids: use them. Otherwise: `diff-screens --base <merge-base with the default branch>`; list
     `unmapped` files in the report as areas without a screen.
5. `python3 $S/coverage.py plan --config .fresh-eyes/config.json --preflight $RUN/preflight.json --mode quick
   --screens a,b --out $RUN/coverage.json`. Tell the user the cell count and the unchecked items in one line.
6. Smoke: navigator on the first cell of each ready platform. If it ends `blocked`, stop and report why.
7. Navigation: one `fresh-eyes:navigator` per platform (they may run in parallel), ≤15 cells per call, until
   `coverage.py next` is empty. Prompt: run folder, config path, platform, device id and name from preflight,
   the cell ids.
8. Review: one `fresh-eyes:reviewer` per screen × platform with done cells (up to 4 in parallel). Prompt: the
   pack folders, `.fresh-eyes/spec.md` + the screen id and its `neighbors` ids (their rules often apply), `.fresh-eyes/known.md`, `.fresh-eyes/examples/`, the
   `references` from config. Journey cells get one reviewer per platform over `<run>/<platform>/journey/`;
   its findings carry the real screen they concern.
9. Verify: `fresh-eyes:verifier` with all `findings.json` paths. If it answers `needs: requests`, run the
   navigator in requests mode with `$RUN/requests.json`, then the verifier again (second pass).
10. `python3 $S/coverage.py verdict --cov $RUN/coverage.json --findings <all findings.json> --language <config>`
    and `python3 $S/coverage.py map --cov $RUN/coverage.json --language <config>`.
11. Write `$RUN/report.md` by `$R/report-format.md` (see "Report").
12. Review with the user, then write the backlog and regression flows (see "After the report").

## full

Goal: before a release, everything from a fresh install. Same as quick, with these differences:
- `--mode full`: release builds from `builds.full`; preflight checks the API inside the built bundle.
- Screens: all of `config.screens`; also walk every `journeys[]` entry end to end (mutating journeys only
  when preflight allowed mutation).
- Start from a fresh install on the owned devices (`devices.py app --do clear` then launch); sign-in must
  already be possible without secrets in prompts (a seeded session or a hook) — otherwise cells are blocked.
- The navigator gets one journey or one screen per call.
- `full --resume`: take the newest `.fresh-eyes/runs/*-full/`, re-run preflight into it, and continue with
  `coverage.py next` until no todo cells remain. Never restart finished cells.

## bench

Measures whether the skill catches known bugs. Not available until `.fresh-eyes/bench/cases.json` exists; see
the plugin README for the benchmark procedure.

## Report

`report.md` in the project language: the verdict line and reasons (verbatim from `coverage.py verdict`),
build/backend/devices, the coverage map, "not checked and why" (preflight `unchecked`, plan `unchecked`,
blocked cells with notes, unmapped files, platform gaps from `$R/platforms.md`), confirmed bugs, improvements,
"look with your own eyes" (unsure), questions. Link evidence crops by path. Keep it scannable.

## After the report

1. Show the user a numbered list: confirmed bugs and improvements (title, screen, platform, severity, crop
   path). Ask which are real and which are intended. Default if they just say ok: all confirmed are real.
2. Real ones → backlog: `python3 $S/backlog.py attach` the main crop into `backlog.images_dir`, then
   `python3 $S/backlog.py append --file <backlog.path> --finding <one finding as JSON> --template <template>`.
   For `quick BUG-N`: `backlog.py set-status --id BUG-N --status verified|open --note "<date>, <device>,
   <build>: <result>, <evidence>"`.
3. Intended ones → append to `.fresh-eyes/known.md`; record "rejected by user" counts in `report.md`
   (this is the false-positive measure).
4. For each real BUG write a regression flow `<maestro_dir>/fresh-eyes-<BUG-id>.yaml` (tag `fresh-eyes`): reach
   the state, run the probe, assert the expected state (`assertVisible` / `assertNotVisible` /
   `extendedWaitUntil`). Visual-only bugs get a comment that `quick` re-checks them.
5. Propose a spec rule for bugs that revealed missing intent; write it only after the user agrees.
6. Final message: verdict, counts, the report path, open questions. Nothing else.
