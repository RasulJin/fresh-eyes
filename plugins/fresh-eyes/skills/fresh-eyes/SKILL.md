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
   the cell ids.
8. Review: one `fresh-eyes:reviewer` per screen × platform with done cells (up to 4 in parallel). Prompt: the
   pack folders, `.fresh-eyes/spec.md` + the screen id and its `neighbors` ids (their rules often apply), `.fresh-eyes/known.md`, `.fresh-eyes/examples/`, the
   `references` from config. Journey cells get one reviewer per platform over `<run>/<platform>/journey/`;
   its findings carry the real screen they concern. More than ~8 packs for one screen: split them between
   reviewers, each writing its own `findings-<k>.json` in the screen folder.
9. Verify: one `fresh-eyes:verifier` per screen folder that has pending findings (up to 4 in parallel); the
   prompt is `first pass`, that folder, `.fresh-eyes/spec.md`, `.fresh-eyes/known.md`. Folders whose verifier
   answered `needs: requests`: one navigator per platform in requests mode with their `requests.json` paths;
   when every entry has `done` or `failed`, a fresh verifier per such folder with `second pass`.
10. `python3 $S/coverage.py verdict --cov $RUN/coverage.json --findings <all findings*.json> --language <config>`
    and `python3 $S/coverage.py map --cov $RUN/coverage.json --language <config>`. If the verdict names done
    cells no reviewer looked at, send those packs to a reviewer (`findings-<k>.json`), verify, and rerun it.
11. Write `$RUN/report.md` by `$R/report-format.md` (see "Report").
12. Review with the user, then write the backlog and regression flows (see "After the report").

## full

Goal: before a release, everything from a fresh install. Same as quick, with these differences:
- `--mode full`: release builds from `builds.full`; preflight checks the API inside the installed app's bundle.
- Screens: all of `config.screens`; also walk every `journeys[]` entry end to end (mutating journeys only
  when preflight allowed mutation).
- Fresh install on the owned devices: `devices.py app --do clear` (Android wipes the data; iOS uninstalls, so
  follow with `--do install --artifact <builds.full.ios.artifact>`), then launch. Sign-in: the navigator types
  `nav.py type --env <accounts.<role>.email_env>` and `--env <…password_env>` (the values live in the user's
  environment, never in prompts or files). No `accounts` in config → keep the installed state instead of
  clearing, and say so in the report.
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
2. Real ones → backlog: write the finding to `$RUN/backlog-<k>.json`, then `python3 $S/backlog.py append
   --file <backlog.path> --finding $RUN/backlog-<k>.json --template <template> --attach $RUN/<its main crop>
   --images-dir <backlog.images_dir>` (assigns the id, copies the crop next to the backlog — run folders are
   git-ignored — and links it). For `quick BUG-N`: `python3 $S/backlog.py set-status --file <backlog.path>
   --id BUG-N --status verified|open --note "<date>, <device>, <build>: <result>, <evidence>"`.
3. Intended ones → append to `.fresh-eyes/known.md`; record "rejected by user" counts in `report.md`
   (this is the false-positive measure).
4. For each real BUG write a regression flow `<maestro_dir>/fresh-eyes-<BUG-id>.yaml` (tag `fresh-eyes`): reach
   the state, run the probe, assert the expected state (`assertVisible` / `assertNotVisible` /
   `extendedWaitUntil`). Visual-only bugs get a comment that `quick` re-checks them.
5. Propose a spec rule for bugs that revealed missing intent; write it only after the user agrees.
6. Final message: verdict, counts, the report path, open questions. Nothing else.
