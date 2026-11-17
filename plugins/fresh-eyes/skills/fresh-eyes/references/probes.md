# Probe library

A probe is the action that makes a hidden bug visible: the navigator runs it, the scripts capture it, the
reviewer judges it. `S` = `${CLAUDE_PLUGIN_ROOT}/skills/fresh-eyes/scripts`. Every probe call names its coverage
cell (`--coverage <run>/coverage.json --cell <id>`); mutating probes exist only when preflight allowed mutation.

Fast probes go straight to the device (`taps.py`, both platforms): a model round-trip or a Maestro flow (which
waits for the screen to settle) is too slow to reproduce races. Navigation to the state goes through `nav.py`.
iOS input goes through the Maestro XCTest driver: rapid taps land ~225 ms apart there, Back is a left-edge swipe.

### none
Just look. `capture.py snap` (Android: `"app_running": false` in its output and meta.json is a crash, its log
goes to the pack's `logcat.txt`), with `--keyboard` when an input is focused. On the `default` state add
`--pages`: the snap then scrolls the screen's list half a screen at a time into `scroll-<k>/` and back, so the
checks see the whole screen. `scroll_stop` says why the pages ended: `end`, `limit`, `keyboard covers list`
(drags stay above an open keyboard), or the page snap that failed. `"scroll_restored": false` in its output:
swipe the list back to the top with `nav.py swipe` (slow, short drags) before the next cell.

### rapid-tap
- Reveals: double sheets/dialogs, double send, double navigation (stacked screens).
- Target: the primary action that opens or sends something (Share, Send, Submit, Buy), never `⋯`, Back or Close.
- `python3 $S/taps.py rapid --platform P --device ID --x X --y Y --count 3 ...` (120 ms apart, a person's fast
  taps; it returns 6 s after the first, so a late system sheet or browser is on the video) wrapped in `capture.py
  around` to record (x, y in screenshot pixels: the center of the node in a fresh `nav.py screen` taken right
  before; rows move after state changes, never reuse them). `--interval-ms 30` only when a request asks for it.
- Oracle: one Back/dismiss later nothing of it is left on screen: `taps.py back` once, then
  `capture.py snap --out <pack>/afterback` (`coverage.py set` refuses the cell without it).
- Control: `--count 1` into `<pack>/single` and `<pack>/single/afterback`. Mutating only if the button sends.

### tap-outside
- Reveals: cards/sheets/menus that do not dismiss, scrims that pass taps through, sheets sliding under cards.
- `taps.py outside --x X --y Y` at a neutral point outside the overlay (scrim / map area).
- Evidence: `capture.py around` (video) + snap after. Oracle: overlay gone, nothing underneath moved wrongly.
- Then, for each surface the screen's `drag` lists (config), open the overlay again and drag that surface (a
  bottom sheet up by its handle, the list under a card) with `nav.py swipe` under `capture.py around`, snapped
  into `<pack>/drag-<n>/` (`coverage.py set` refuses the cell without them). Oracle: the overlay closes, or the
  dragged surface stays fully usable; a sheet sliding under a card that keeps covering it is a BUG.

### back-during-animation
- Reveals: half-open states, invisible windows that eat taps, jumps.
- `taps.py tap-back --x X --y Y --delay-ms 100` (x, y: the control that opens the sheet/menu/screen, from a
  fresh `nav.py screen`): the tap and Back go out in one command, Back lands mid-transition (iOS: the edge
  swipe, no sooner than ~225 ms). Repeat with `--delay-ms 50` and `150`. Then tap the content behind.
- Evidence: `capture.py around` around each `tap-back`, `--out <pack>/d100`, `<pack>/d50`, `<pack>/d150` (the
  delay in the name; `coverage.py set` reads these sub-folders). It refuses the cell when no recorded frame
  moved (Back landed before the transition began): repeat with a longer `--delay-ms`, or on Android widen the
  window with `devices.py set --what slow-animations` (restore with `--what animations-on`).

### hardware-back
- Reveals: Back closing the wrong layer, losing unsaved input silently, exiting from step 2 of a wizard.
- `taps.py back` (iOS: left-edge swipe; sheets there close by swiping down, `nav.py swipe`). Matrix: with a sheet
  open, a dialog open, on a root screen after cold start. iOS: a screen without a visible way out is a bug.
- Oracle: the topmost layer closes first; unsaved input asks or is kept.

### hardware-back-keyboard
- Reveals: Back with the keyboard open closing the whole sheet and losing typed text.
- Android only: focus a field, type, `taps.py back` once. Oracle: only the keyboard closes; text is still there.

### background-foreground
- Reveals: lost drafts/replies/results, a request that dies in background, double charges on resume.
- Android: `devices.py app --do background` (Home, wait, relaunch). Stock emulators keep sockets alive in the
  background; to imitate aggressive OEMs also toggle the network off while in background (own devices only).
- iOS: the same call (opens Settings, waits, relaunches). Best during a long request (send, then background).

### kill-relaunch
- Reveals: state that should persist (session, drafts, theme, onboarding done) but does not; welcome flashes.
- `capture.py around --out <pack> -- python3 $S/devices.py app --platform P --device ID --app <app id> --do
  relaunch`: it stops the app, starts it and returns 4 s after its first frame, so the video holds the splash.

### keyboard-open
- Reveals: what the keyboard or a footer on it hides; focus without keyboard; a field that jumps twice or lags.
- Steps: focus the main input under `capture.py around --out <pack>/kb-show -- python3 $S/nav.py tap ...`, then
  `capture.py snap --keyboard --before <pack before focus>`; Android: Back only hides it, recorded with
  `capture.py around --out <pack>/kb-hide -- python3 $S/taps.py back ...`; `keyboard.json` comes by itself
  (after the snap, again after kb-hide). Then the other inputs; type with real key taps when the IME matters.
- Focus it again, leave (a link, avatar or tab above the keyboard, not Back; a modal: close and reopen), snap
  where you landed into `<pack>/left` (the screen's marker gone) and return; keyboard up again: one `taps.py
  back`, wait 1.5 s; `capture.py snap --out <pack>/return` (a stale inset). `coverage.py set` needs
  `keyboard.json`, `left` and that snap with the keyboard closed. Open/close 3–4 times: does the form creep?

### search
- Reveals: suggestions in mixed languages, wrong or duplicate matches, results that contradict the form or each
  other (prices, counts), labels and screen-reader names stuck after the request ("busy").
- Steps: type 3–4 chars of a real name into the search field (`nav.py type`, Latin: non-ASCII is slow on
  Android) and `capture.py snap --keyboard --out <pack>/typed` (required). Pick a suggestion, fill the rest, tap
  the primary button that only reads (Search, Find, Show, Filter), wait for the results and snap the pack
  itself. Results on another screen: also Back and snap the form into `<pack>/form-after`.
- Mutating: no. A button that writes data (Send, Save, Pay) is `double-submit`, never this probe.

### scenario
- Reveals: logic and flow bugs no single screen shows: results of the old input under a changed form, a hidden
  side effect (something saved without asking), invalid input accepted (from = to, letters in a number), a
  stopped or failed action left unmarked, a retry that duplicates, an edit that loses focus or text, state
  gone or stale after coming back, app errors in the log while the screen looks fine.
- Before: `capture.py logs --platform android --device ID --out PACK --package PKG --clear` (PKG: `app.android_id`).
- Steps: snap after each into `<pack>/step-<n>/` and write `<pack>/steps.json`:
  `[{"n": 1, "kind": "task", "did": "...", "expect": "..."}]`, with `expect` written before you look; a step
  whose action left the screen as it was (an invalid input refused in place) also gets `"unchanged": true`.
  1. `task`: do what the screen is for (the screen's `task` in config) end to end with realistic input, and use
     each primary action the screen offers on the way (the buttons on a result or card), not one path only;
     wait for each result. Go through every form the screen opens itself (config `forms`: a header button's
     sheet, a picker, an edit mode), one `task` step each, naming it in `via`.
  2. `change`: after the result, change one input and run again (another value, an edited item, a new question).
  3. `invalid`: one invalid or edge input (equal values in fields that must differ, empty, letters, too long).
  4. `interrupt`, twice (a tab switch is not one): `how: "stop"`: stop a running action with the screen's own
     Stop or Cancel (Back only when it has none), then retry it; `how: "offline"` (Android): `devices.py set
     --what offline`, run the action, snap what the app says, `devices.py set --what online`, then retry.
  5. `check`: leave and come back, then open where the result should live (the `task` says where): is it there,
     and is nothing there that was not asked for?
- Look first, touch second: after each action snap before you touch anything. Never tap the field the app
  should have focused, never close what the app opened: the snap must show what the app did by itself.
- After: `capture.py logs --platform android --device ID --out PACK --package PKG` (writes `logcat.txt`).
- Data: the cell is `"mutating": true` exactly when preflight allowed mutation. Then really send, edit and
  delete test data (a comment, a message, a saved item) so the edit and delete forms run; never send anything
  to real people. `steps.json` says so: the step that writes gets `"wrote": "<what was created or changed>"`,
  the `check` step `"check": "<what was compared>"` and `"found_in": "<where it was found>"`, the clean-up step
  `"cleaned": "<what was deleted>"`. A read-only walk is for `"mutating": false` only; a mutating cell that
  cannot write is `blocked` with why, never done.
- `coverage.py set` refuses the cell unless `steps.json` has all five kinds (a mutating one also `wrote`,
  `found_in` and `cleaned`), both interruptions (iOS: stop), a `task` step for each form that has its own snap
  in `<pack>/step-<n>/` (a step that did not run does not count), and, on Android, `logcat.txt`.

### swipe-inside
- Reveals: content clipped by its container (a grid that cannot scroll to its last row), pagers that snap back,
  jumps when releasing halfway.
- Steps: snap into `<pack>/before` → `nav.py swipe` inside the container's bounds → snap the pack itself with
  `--before <pack>/before` (exact paths: navigator.md); `geometry.still_clipped` flags rows still cut.
  `coverage.py set` refuses a pack snapped without `--before`, and one where the swipe moved nothing: swipe
  inside the list itself; too few items to scroll is `--status na` with note "not enough items", never done.
- On a page of a pager or tab set, for each config `pages` entry k: open that page by its tab, wait 1.5 s, come
  back by this screen's tab, swipe the content up to its end, snap into `<pack>/page-<k>/before`, swipe once more
  and snap into `<pack>/page-<k>` with `--before` that. A page laid out while a shorter one was open can stay
  held to that height: the grid ends in a row cut to a strip with blank under it, and `still_clipped` flags it.
- Pagers, drawers and sheets get `drag-interrupt` for the slow drag and the races.

### edge
- On a verifier's request only (`repeat-probe`, `args` `--node nN --side top --dp 2`), never planned: does a target
  drawn under the minimum answer just outside its drawn edge? uiautomator cuts bounds to the parent.
- Bring the screen to the pack's state (the node at the same bounds in `nav.py screen`), then `capture.py around
  --out <pack>-recheck -- python3 $S/taps.py edge --pack <pack> --node nN --side S --dp 2 ...` and `capture.py snap
  --out <pack>-recheck --before <pack>`. Side: where its parent clips the node, else its short side.
- Oracle: the node itself responds (its icon toggles, its action opens), not just any change on screen.

### drag-interrupt
- Reveals: invisible windows left after a drag closes something (the next tap is eaten), Back mid-drag reopening
  it or leaving the content dead or shifted, pagers and drawers that jump past halfway or snap back.
- Android only. Target: the screen's `draggable` in config (a drawer, a pager, a bottom sheet), x, y from a fresh
  `nav.py screen`. Each step is one `taps.py` call with `--pack <pack>`: it snaps `<pack>/<step>/before` (on the
  screen's marker, else refused: bring the screen to rest, run again), records the gesture into `<pack>/<step>`
  and snaps where it left 1.5 s later; never snap these yourself. `<pack>/steps.json` as in `scenario`:
  1. `slow`: relaunch the app (`devices.py app --do terminate`, then `--do launch`), reach the screen; as the
     first touch `taps.py slow --from X,Y --to X,Y` (`--to`: the far end of a drag across the whole surface; it
     drags 40 %, then 60 %). Oracle: under half it springs back, over half it moves on; it never sticks between.
  2. `drag-back`: `taps.py drag-back --from X,Y --to X,Y --ms 2000 --back-at-ms 700` (Back while the finger is
     down), then `taps.py tap --label <the content behind> --after <pack>/drag-back/tap`. Oracle: Back closed it
     (or did what it does without the drag), and the content answers the tap.
  3. `swipe-tap`: with it open, `taps.py swipe-tap --from X,Y --to X,Y --x X --y Y` (the drag that closes it,
     then, the run's `motion_ceiling_ms` after the finger lifts, a tap on something behind it that visibly
     reacts). Oracle: the tap landed: by then the closing animation is over. A tap that works only later is a BUG.

### tap-everything
- Reveals: dead taps, wrong destinations, labels that lie ("Follow" opening the profile), disabled controls
  that look enabled.
- Steps: `taps.py all --pack <pack> --cell <cell>` taps every clickable node of the pack's `som.json` in one
  call, skipping destructive labels (delete, remove, log out, pay, send). One node, a skipped one or a recheck:
  `taps.py tap --pack <pack> --som N` (or `--id`/`--label`; `--x --y` only with `--expect-id`), then go back.
  Each tap finds its node in a dump taken just before the tap (refused: gone, off screen, under the keyboard),
  waits until the screen settles (≤3 s), snaps the result with `tap.json` into `<pack>/tap-<k>` and appends row k
  of `taps.json` (label, `changed`, `result`) itself. `--record` on a label that promises a short reply (Copy,
  Like, Save): a 2 s toast is gone by the snap, the video keeps it.
- Chips that send a message (a chat's prompts): skip them here (config `skip_tap`); `scenario` presses them.
- Three `"changed": false` in a row: something covers the screen (a photo viewer, a sheet left open). Close it,
  reach the screen, tap those again; `coverage.py set` refuses such a run of three unless `--note` says why.
- Reply/comment buttons and inputs only focus a field, they change no data: tap them too, and first, so the
  later taps leave and return with the keyboard having been up.

### rotate
- Reveals: layouts that break or lose state on rotation (only if the app supports landscape).
- `devices.py set --what landscape`, back with `--what portrait` (iOS goes through the Maestro driver).

### offline
- Reveals: offline shown as "empty"/"not found", zero counters, lying banners.
- Android: `devices.py set --what offline`, snap the screen, restore with `--what online` (wifi/data toggles:
  after airplane mode an emulator can lose DNS until restarted).

### replay
- `quick BUG-N`: replay the backlog entry's steps into `<run>/<platform>/recheck-BUG-N/default-replay/`
  (snap after each step as in a journey), so the reviewer can compare with its "Expected" field.
- Steps that create or change data only when preflight allowed mutation; otherwise mark the cell blocked.

### edit-delete-restore
- Mutating. Reveals: indicators that stay after editing (a "✓ selected" next to changed text), previews that
  differ from what is saved, restored items that stay grey or "not found".
- Steps: create a test item → edit it → save → reopen; delete → trash → restore → back to the list.
- Only on staging/local with a test account; clean up what was created.

### double-submit
- Mutating. Reveals: duplicate posts/comments/messages from fast double taps on Send/Save.
- Steps: fill the form, `taps.py rapid` on the submit button, then check the list for duplicates.
- Only on staging/local with a test account; clean up what was created.
