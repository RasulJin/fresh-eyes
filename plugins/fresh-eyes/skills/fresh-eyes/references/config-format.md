# `.fresh-eyes/config.json`

JSON because the scripts use only the Python 3.9 standard library. Paths are relative to the repo root unless
noted. Example (generic values):

```json
{
  "platforms": ["android", "ios"],
  "language": "en",
  "app": {"android_id": "com.example.app", "ios_id": "com.example.app", "expo_root": "apps/mobile"},
  "env": {
    "name": "staging",
    "api_expected": "https://api-staging.example.com",
    "api_forbidden": ["https://api.example.com"]
  },
  "accounts": {
    "author": {"email_env": "FE_AUTHOR_EMAIL", "password_env": "FE_AUTHOR_PASSWORD"},
    "viewer": {"email_env": "FE_VIEWER_EMAIL", "password_env": "FE_VIEWER_PASSWORD"}
  },
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
  "backlog": {"path": "docs/qa-backlog.md", "template": "en-backlog", "images_dir": "docs/qa-backlog"},
  "maestro_dir": "apps/mobile/.maestro",
  "references": ["Threads", "Instagram"],
  "motion_ceiling_ms": 300,
  "screens": [
    {"id": "profile-trips", "name": "Profile → Trips", "route": "app/(tabs)/profile.tsx",
     "reach": "Open the Profile tab, then the Trips tab of the pager.",
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
  relative to it, and the `@/` import alias resolves to it.
- `env`: the backend the run must use. `api_expected` must be found in the build (quick: `metro_env`, full:
  inside the built bundle) and no `api_forbidden` URL may be present, otherwise mutating probes are disabled.
  Only `name` `staging` or `local` can enable mutation.
- `accounts`: names of environment variables holding test credentials. Values never go into files or reports.
- `builds.quick|full.<platform>`: `build` (command, run in `cwd`), `install` (optional), `start` (Metro),
  `metro_env` (env for Metro), `artifact` (built file for full mode).
- `hooks`: optional project commands: `seed` creates data for states like `empty`/`long-text`, `cleanup`
  removes what the run created, `new_account` creates a fresh test account. Without `seed` those states are
  reported as not checked.
- `backlog`: where confirmed findings go; `template` is `ru-backlog` or `en-backlog`; `images_dir` gets crops.
- `maestro_dir`: existing flows (reused as setup steps) and where regression flows are written.
- `references`: apps the user compares against; the UX rubric uses them.
- `motion_ceiling_ms`: transition duration ceiling from the design system.
- `screens[]`: `id`, `name`, `route`, `reach` (how to get there: words or a flow path), `states` (from the
  checklist), `probes` (from the probe library), `neighbors` (screens a change here can break).
- `journeys[]`: end-to-end paths for full mode; `mutating: true` journeys only run when mutation is allowed.
