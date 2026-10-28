# Probe library

A probe is the action that makes a hidden bug visible. The navigator runs it, the scripts capture it, the
reviewer judges it. `S` = `${CLAUDE_PLUGIN_ROOT}/skills/fresh-eyes/scripts`. Every probe call names its
coverage cell (`--coverage <run>/coverage.json --cell <id>`); mutating probes only exist in the plan when
preflight allowed mutation.

Fast probes go straight to the device (`taps.py`, both platforms): a model round-trip or a Maestro flow (which
waits for the screen to settle) is too slow to reproduce races. Navigation to the state goes through `nav.py`.
iOS input goes through the Maestro XCTest driver, which serializes touches: rapid taps land ~225 ms apart there
(enough for double-submit windows, not for a sub-100 ms race), and Back is a left-edge swipe.

### none
Just look. `capture.py snap`. With `--keyboard` when an input is focused.

### rapid-tap
- Reveals: double sheets/dialogs, double send, double navigation (stacked screens).
- Target: the primary action that opens or sends something (Share, Send, Submit, Buy), never `⋯`, Back or Close.
- `python3 $S/taps.py rapid --platform P --device ID --x X --y Y --count 3 --interval-ms 30 ...` wrapped in
  `capture.py around` to record (x, y in screenshot pixels: the center of the node in a fresh `nav.py screen`
  taken right before; rows move after state changes, so never reuse coordinates from an earlier screen).
- Oracle: one Back/dismiss later nothing of it is left on screen: `taps.py back` once, then
  `capture.py snap --out <pack>/afterback` (`coverage.py set` refuses the cell without it).
- Mutating: no (but if the button sends something, the probe belongs to a mutating cell).

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
  fresh `nav.py screen`): the tap and Back go out in one command, Back lands mid-transition (iOS: the
  edge swipe, no sooner than ~225 ms). Repeat with `--delay-ms 50` and `150`. Then tap the content behind to
  check it responds.
- Evidence: `capture.py around` around the whole sequence. Optional on Android: widen the window with
  `devices.py set --what slow-animations` (restore with `--what animations-on`).

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
- iOS: the same call (opens Settings, waits, relaunches the app).
- Best done during a long request (send, then background immediately).

### kill-relaunch
- Reveals: state that should persist (session, drafts, theme, onboarding done) but does not; welcome flashes.
- Android: `am force-stop`, then launch under `capture.py around`. iOS: `simctl terminate` + `launch`.

### keyboard-open
- Reveals: fields, counters, suggestions and CTAs under the keyboard; focus without keyboard.
- Steps: tap each input; `capture.py snap --keyboard --before <pack before focus>` so new suggestion nodes
  hidden under the keyboard are caught (no keyboard at all → a `keyboard_missing` candidate); type a few chars with real key taps when the bug may be in the IME.
- Then, with the keyboard up, leave the screen (a link, avatar or tab, not Back; a sheet or modal with no way
  out: close and reopen it) and return. If the keyboard is up again (a tab keeps the field focused), close it
  with one `taps.py back` (with the keyboard up Back only hides it), wait 1.5 s, then
  `capture.py snap --out <pack>/return`: a bar or gap left at keyboard height is a stale inset. `coverage.py set`
  refuses the cell without this snap, and refuses a `return` snap that still shows the keyboard.
- Also open/close the keyboard 3–4 times and compare the bottom of the form (layout creep).

### search
- Reveals: suggestions in mixed languages, wrong or duplicate matches, results that contradict the form or each
  other (prices, counts), labels and screen-reader names stuck after the request ("busy").
- Steps: type 3–4 chars of a real name into the search field (`nav.py type`, Latin: non-ASCII is slow on
  Android) and `capture.py snap --keyboard --out <pack>/typed` (`coverage.py set` refuses the cell without
  it). Pick a suggestion, fill the rest, tap the primary button that only reads (Search, Find, Show, Filter),
  wait for the results and snap the pack itself. Results on another screen: also Back and snap the form into
  `<pack>/form-after`.
- Mutating: no. A button that writes data (Send, Save, Pay) is `double-submit`, never this probe.

### scenario
- Reveals: logic and flow bugs no single screen shows: results of the old input under a changed form, a hidden
  side effect (something saved without asking), invalid input accepted (from = to, letters in a number), a
  stopped or failed action left unmarked, a retry that duplicates, an edit that loses focus or text, state
  gone or stale after coming back, app errors in the log while the screen looks fine.
- Before: `capture.py logs --platform android --device ID --out PACK --package PKG --clear` (PKG =
  `app.android_id`).
- Steps: snap after each into `<pack>/step-<n>/` and write `<pack>/steps.json`:
  `[{"n": 1, "kind": "task", "did": "...", "expect": "..."}]`, with `expect` written before you look.
  1. `task`: do what the screen is for (the screen's `task` in config) end to end with realistic input, and use
     each primary action the screen offers on the way (the buttons on a result or card), not one path only;
     wait for each result. Go through every form the screen opens itself (config `forms`: a header button's
     sheet, a picker, an edit mode), one `task` step each, naming it in `via`.
  2. `change`: after the result, change one input and run again (another value, an edited item, a new
     question).
  3. `invalid`: one invalid or edge input (the same value in two fields that must differ, empty, letters in a
     number, very long).
  4. `interrupt`, twice (a tab switch is not one): `how: "stop"`: stop a running action with the screen's own
     Stop or Cancel (Back only when it has none), then retry it; `how: "offline"` (Android):
     `devices.py set --what offline`, run the action, snap what the app says, `devices.py set --what online`,
     then retry.
  5. `check`: leave and come back, then open where the result should live (the `task` says where): is it there,
     and is nothing there that was not asked for?
- Look first, touch second: after each action snap before you touch anything. Never tap the field the app
  should have focused, never close what the app opened: the snap must show what the app did by itself.
- After: `capture.py logs --platform android --device ID --out PACK --package PKG` (writes `logcat.txt`).
- Data: the cell is `"mutating": true` exactly when preflight allowed mutation. Then really send, edit and
  delete test data (a comment, a message, a saved item) so the edit and delete forms run, and clean up what you
  created; never send anything to real people. Otherwise do the read-only part and say so in `steps.json`.
- `coverage.py set` refuses the cell unless `steps.json` has all five kinds, both interruptions (iOS: stop), a
  `task` step for each form that has its own snap in `<pack>/step-<n>/` (a step that did not run does not
  count), and, on Android, `logcat.txt`.

### swipe-inside
- Reveals: content clipped by its container (a grid that cannot scroll to its last row), pagers that snap back,
  jumps when releasing halfway.
- Steps: snap → `nav.py swipe` inside the container's bounds → `capture.py snap --before <first pack>`;
  `geometry.still_clipped` flags rows still cut. `coverage.py set` refuses a pack snapped without `--before`,
  and one where the swipe moved nothing (it missed the list): swipe inside the list itself. A container that
  truly does not scroll goes in `--note`.
- For pagers also: slow drag (1.5–2.5 s) released before halfway; strong fling; under `capture.py around`.

### tap-everything
- Reveals: dead taps, wrong destinations, labels that lie ("Follow" opening the profile), disabled controls
  that look enabled.
- Steps: for each clickable node in `nodes.json` (skip destructive labels: delete, remove, log out, pay, send),
  tap it, snap, go back. Record "tapped N → screen/state" in the pack's `taps.json`.
- Reply/comment buttons and inputs only focus a field, they change no data: tap them too, and first, so the
  later taps leave and return with the keyboard having been up.

### rotate
- Reveals: layouts that break or lose state on rotation (only if the app supports landscape).
- `devices.py set --what landscape`, back with `--what portrait` (iOS goes through the Maestro driver).

### offline
- Reveals: offline shown as "empty"/"not found", zero counters, lying banners.
- Android: `devices.py set --what offline`, snap the screen, restore with `--what online`.
- Note: after airplane mode an emulator can lose DNS until restarted; prefer wifi/data toggles.

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
