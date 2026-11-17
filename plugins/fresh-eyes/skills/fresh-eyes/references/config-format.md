# `.fresh-eyes/config.json`

JSON because the scripts use only the Python 3.9 standard library. Paths are relative to the repo root unless
noted. Example (generic values):

```json
{
  "platforms": ["android", "ios"],
  "language": "en",
  "app": {"android_id": "com.example.app", "ios_id": "com.example.app", "expo_root": "apps/mobile", "locale": "en-US"},
  "env": {
    "name": "staging",
    "api_expected": "https://api-staging.example.com",
    "api_forbidden": ["https://api.example.com"]
  },
  "accounts": {
    "author": {"email_env": "FE_AUTHOR_EMAIL", "password_env": "FE_AUTHOR_PASSWORD"},
    "viewer": {"email_env": "FE_VIEWER_EMAIL", "password_env": "FE_VIEWER_PASSWORD"}
  },
  "consents": [{"text": "Community guidelines", "button": "Accept"}, {"text": "is an AI", "button": "Got it"}],
  "builds": {
    "quick": {
      "android": {"build": "npx expo run:android --device fresh-eyes-pixel", "cwd": "apps/mobile",
                  "start": "npx expo start --dev-client", "metro_env": {"EXPO_PUBLIC_API_URL": "https://api-staging.example.com"}},
      "ios": {"build": "npx expo run:ios --device \"fresh-eyes iPhone 17\"", "cwd": "apps/mobile",
              "start": "npx expo start --dev-client", "metro_env": {"EXPO_PUBLIC_API_URL": "https://api-staging.example.com"}}
    },
    "full": {
      "android": {"build": "EXPO_PUBLIC_API_URL=https://api-staging.example.com npx expo run:android --variant release",
                  "cwd": "apps/mobile", "artifact": "apps/mobile/android/app/build/outputs/apk/release/app-release.apk"}
    }
  },
  "hooks": {"seed": null, "cleanup": null, "new_account": null},
  "backlog": {"path": "docs/qa-backlog.md", "template": "en-backlog", "images_dir": "docs/qa-backlog",
              "extra": {"cmd": "board list --open --format tsv"}},
  "maestro_dir": "apps/mobile/.maestro",
  "references": ["Threads", "Instagram"],
  "motion_ceiling_ms": 300,
  "screens": [
    {"id": "profile-trips", "name": "Profile → Trips", "route": "app/(tabs)/profile.tsx",
     "reach": "Open the Profile tab, then the Trips tab of the pager.", "marker": ["Мои поездки", "trips-list"],
     "task": "Open a trip; a new trip appears here after it is created.",
     "states": ["default", "empty", "long-text", "dark", "font-xl", "small-screen"],
     "probes": ["swipe-inside", "tap-everything"], "neighbors": ["profile-posts"]}
  ],
  "journeys": [
    {"id": "share-a-post", "steps": "Feed → open a post → Share → dismiss", "screens": ["feed", "post"], "mutating": false}
  ]
}
```

## Fields

- `platforms`: which platforms the project targets. Missing platforms are not reported as unchecked.
- `language`: report and backlog language (`ru`, `en`).
- `app.*_id`: bundle id / package; `app.expo_root`: where `app/` (Expo Router) lives; screen routes are
  relative to it, and the `@/` import alias resolves to it. `app.locale` (optional, a tag like `ru-RU`): the
  language the app is tested in, the one the spec's texts and screen markers are written in; a wiped app is set
  to it before launch, whatever the device language.
- `env`: the backend the run must use. Preflight reads the JS bundle the app really runs (inside the app
  installed on the device, or the one Metro serves it): `api_expected`'s host must be in it and no
  `api_forbidden` host may be, otherwise mutating probes are disabled. Full mode needs a release build (bundle
  inside the app). Only `name` `staging` or `local` can enable mutation.
- `accounts`: names of environment variables holding test credentials. Values never go into files or reports.
  The first role is the one a navigator signs in with when it finds the app signed out. Export the variables in
  the shell that starts Claude Code (a headless `claude -p` run inherits them), or, for sessions that do not see
  them (the desktop app), keep the values in the macOS keychain under services `fresh-eyes-<role>-email` and
  `fresh-eyes-<role>-password`, once per value:
  `security add-generic-password -U -a "$USER" -s fresh-eyes-viewer-email -w` (it asks for the value; nothing
  lands in shell history). A set variable wins over the keychain. Preflight is not ready while a value is in
  neither.
- `consents` (optional): the consent dialogs the owner lets the test accounts accept (terms, community rules,
  an AI or data-sharing notice): `text`, a part of the dialog's text (its title), and `button`, the label to
  press. The navigator presses an accepting button only on a dialog listed here; any other consent dialog that
  stands in a cell's way blocks the cell with note `consent: <its text>`. Preflight prints the list.
- `builds.quick|full.<platform>`: `build` (command, run in `cwd`), `install` (optional), `start` (Metro),
  `metro_env` (env for Metro), `artifact` (built file to install: `preflight.py --fix` puts it on both devices
  where no app is, and over another build only with `--source built`; `--apk <path>` names another APK for the
  run and always replaces; preflight.json `installed` says what went where).
- `hooks`: optional project commands, run from the repo root: `seed` creates data for `empty`/`long-text`/`long-list`
  and `cleanup` removes what the run created (the orchestrator runs both, only when preflight allowed
  mutation); `loading` / `error` put the app into that state (e.g. a slow or failing mock), run by the
  navigator right before snapping those cells; `new_account` creates a fresh test account. A state without its
  hook is reported as not checked.
- `backlog`: where confirmed findings go; `template` is `ru-backlog` or `en-backlog`; `images_dir` gets crops.
  `extra` (optional): open tasks kept outside the backlog file (a task board), as a file path or `{"cmd":
  "<command>"}` run from the repo root; each line `<id>\t<title>`. `backlog.py index` adds them to the index, so
  a confirmed BUG one of them holds gets that id as `backlog_id` ("already open: <id>") instead of a new entry.
- `maestro_dir`: existing flows (reused as setup steps) and where regression flows are written.
- `references`: apps the user compares against; the UX rubric uses them.
- `motion_ceiling_ms`: transition duration ceiling from the design system (default 300). `coverage.py plan`
  copies it into coverage.json; `capture.py around` and `taps.py swipe-tap` read it from there.
- `screens[]`: `id`, `name`, `route`, `reach` (how to get there: words or a flow path), `states` (from the
  checklist), `probes` (from the probe library), `neighbors` (screens a change here can break), `marker`
  (1–3 strings only this screen shows: its header title, a unique button label, a testID; matched
  case-insensitively as part of a node's text, label or id). `coverage.py set` refuses a `none` or
  `keyboard-open` pack that shows none of them, unless `--note` says what is on screen.
  `task` (for `scenario`): what a person comes to this screen to do, in a sentence, and where the result should
  show up afterwards (another tab, a list, the same screen after reopening).
  `forms` (for `scenario`): labels of the controls that open the screen's own forms, sheets, pickers or edit
  modes; the scenario must go through each.
  `drag` (for `tap-outside`): surfaces a person drags or scrolls while a card or menu is open over them (a
  bottom sheet by its handle, a list); the probe drags each one with the overlay open.
  `draggable` (for `drag-interrupt`): what a person drags on this screen and which way ("the open side menu,
  leftwards to close it", "the Trips / Posts pager, sideways").
  `pages` (for `swipe-inside`): the other pages of the pager or tab set this screen is a page of, by their tab
  labels ("the Posts tab", "the Saved tab"); the probe opens each, comes back and scrolls to the end.
  `signed_out` (optional, `true`): reached without an account (a welcome screen, a consent document); the plan
  puts these screens first, since a full run starts from cleared data.
- `app_theme`: how to pick the app's own Light, Dark and system ("as in the system") theme, in words (where the
  control is, its labels); the navigator picks the system one before signing out for screens without an account;
  without it the `theme-mismatch` state is reported as not checked. List `theme-mismatch` after `dark`.
- `journeys[]`: end-to-end paths for full mode; `mutating: true` journeys only run when mutation is allowed.
