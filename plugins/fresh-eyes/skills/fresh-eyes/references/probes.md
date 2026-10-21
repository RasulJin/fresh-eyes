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
- `python3 $S/taps.py rapid --platform P --device ID --x X --y Y --count 3 --interval-ms 30 ...` wrapped in
  `capture.py around` to record (x, y in screenshot pixels: the center of the node in a fresh `nav.py screen`
  taken right before; rows move after state changes, so never reuse coordinates from an earlier screen).
- Oracle: one Back/dismiss later nothing of it is left on screen (`capture.py snap`, then compare).
- Mutating: no (but if the button sends something, the probe belongs to a mutating cell).

### tap-outside
- Reveals: cards/sheets/menus that do not dismiss, scrims that pass taps through, sheets sliding under cards.
- `taps.py outside --x X --y Y` at a neutral point outside the overlay (scrim / map area).
- Evidence: `capture.py around` (video) + snap after. Oracle: overlay gone, nothing underneath moved wrongly.

### back-during-animation
- Reveals: half-open states, invisible windows that eat taps, jumps.
- Start the transition in the background (`nav.py tap ... &` or `nav.py swipe ... &`), 50–150 ms later
  `taps.py back` (iOS: the left-edge swipe); then tap the content behind to check it responds.
- Evidence: `capture.py around` around the whole sequence. Optional: widen the window with
  `settings put global animator_duration_scale 5` on own Android devices only (restore to 1 afterwards).

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
- Android: `devices.background_foreground` (Home, wait, relaunch). Stock emulators keep sockets alive in the
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
- Also open/close the keyboard 3–4 times and compare the bottom of the form (layout creep).

### swipe-inside
- Reveals: content clipped by its container (a grid that cannot scroll to its last row), pagers that snap back,
  jumps when releasing halfway.
- Steps: snap → `nav.py swipe` inside the container's bounds → `capture.py snap --before <first pack>`;
  `geometry.still_clipped` flags rows still cut.
- For pagers also: slow drag (1.5–2.5 s) released before halfway; strong fling; under `capture.py around`.

### tap-everything
- Reveals: dead taps, wrong destinations, labels that lie ("Follow" opening the profile), disabled controls
  that look enabled.
- Steps: for each clickable node in `nodes.json` (skip destructive labels: delete, remove, log out, pay, send),
  tap it, snap, go back. Record "tapped N → screen/state" in the pack's `taps.json`.

### rotate
- Reveals: layouts that break or lose state on rotation (only if the app supports landscape).
- Android: `settings put system user_rotation 1` (own devices; accelerometer rotation off), back to 0 after.
- iOS: a Maestro MCP `run` with `setOrientation` (the only MCP use besides starting the driver).

### offline
- Reveals: offline shown as "empty"/"not found", zero counters, lying banners.
- Android: `devices.set_network(dev, False)`, snap the screen, restore with `set_network(dev, True)`.
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
