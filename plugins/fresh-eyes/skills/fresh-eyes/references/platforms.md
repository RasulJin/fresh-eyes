# Platform notes and test-bench traps

## Devices

- fresh-eyes only changes devices it owns: Android AVDs `fresh-eyes-pixel` (port 5560) and `fresh-eyes-small`
  (port 5562), iOS simulators `fresh-eyes iPhone 17` and `fresh-eyes iPhone 16e`. `devices.py ensure` creates
  and boots them. Other devices (the user's emulator, a phone) are read-only to us.
- **Android: one UiAutomation at a time.** While the Maestro MCP driver runs on a device, `uiautomator dump` is
  killed; force-stopping the driver kills the MCP session (it does not recover). So on Android fresh-eyes
  navigates with `nav.py` (uiautomator + adb) and never uses the Maestro MCP.
- **iOS: the Maestro XCTest driver.** One MCP `inspect_screen` on the simulator starts Maestro's driver app, an
  HTTP server on localhost (found by the simulator's UDID in its process path). `nav.py` (with `--platform ios`) and
  `capture.py` use it for the hierarchy (in points; scripts scale to screenshot pixels), taps, swipes and typing
  (any language) — no screen JSON passes through the model. If the MCP server restarts, the driver dies with it:
  the orchestrator calls `inspect_screen` once more (a navigator replies `needs: ios-driver`). The driver serializes touches (~225 ms each).
- Never run the Maestro CLI on a device the Maestro MCP is driving: the CLI uninstalls the MCP's driver on exit
  and every later MCP call fails with `DeviceServerDiedException` until a new device serial is used.
  Recovery: restart the owned emulator on another port (`emulator -avd fresh-eyes-pixel -port 5564`, ownership
  is checked by AVD name, not port) or ask the user to reconnect the Maestro MCP server (`/mcp`).
- Android builds need `ANDROID_HOME` (usually `~/Library/Android/sdk`) in the build command's environment;
  a stale `android/` folder from an older SDK needs `npx expo prebuild -p android` first.

## Traps that make a run falsely green

- **Hardware keyboard mode** hides the soft keyboard and with it a whole class of bugs. Our AVDs use
  `hw.keyboard=no`; preflight sets `show_ime_with_hard_keyboard=1`. On iOS Simulator turn off
  "Connect Hardware Keyboard" (I/O → Keyboard) so the soft keyboard appears.
- **Stylus mode on emulators**: the emulator's touchscreen reports a STYLUS source, so Gboard shows a "Try out
  your stylus" onboarding and then a small handwriting toolbar instead of the keyboard. Keyboard bugs vanish and
  taps near the field can be swallowed. Preflight sets `stylus_handwriting_enabled 0` and restarts Gboard.
- **iOS slide-to-type intro**: the first keyboard on a fresh simulator shows "Набирайте быстрее…/Type faster…"
  with a Continue button instead of keys (no Keyboard node in the hierarchy, so `keyboard_missing` fires). Tap
  Continue once (bottom center of the keyboard area), then redo the keyboard cell.
- **Animations disabled** (`animator_duration_scale=0`, Maestro `disableAnimations`) hides motion bugs.
  Preflight restores scale 1 on own devices.
- **Maestro waits for the screen to settle** before every step and after taps. That masks races; fire fast
  probes with `taps.py` / adb instead.
- **Stock emulators keep sockets alive in background**; real phones from aggressive OEMs kill them. Toggle the
  network during background probes to imitate them.
- **Wrong backend**: local builds often read a production URL from `.env`, and a Metro started without the
  override serves it. Preflight reads the bundle the app really runs (inside the installed app, or the one Metro
  serves it — every running Metro must pass) and looks for the API host there; without a match, mutating
  probes are off. After a Metro check it restarts the app so no JS from an earlier Metro stays in memory.
  Release builds with `expo-updates` on may run a downloaded update instead of the embedded bundle: turn
  updates off in test builds, or the check proves nothing.
- **Stale build**: the installed app must be the build under test (rebuild or reload Metro after changes).
- **First minutes after install** behave differently (caches, permissions, onboarding). Run part of the
  probes right after a fresh install.

## Android vs iOS differences worth probing

| Area | Android | iOS |
|---|---|---|
| Back | hardware/gesture Back; must close the top layer only | edge swipe; no global Back — every screen needs a visible way out |
| Share | system Sharesheet (`package="android"` in the hierarchy) | `UIActivityViewController` sheet; different height and dismissal |
| Keyboard | Gboard; `adjustResize`/edge-to-edge quirks; `KeyboardAvoidingView behavior="height"` creeps | keyboard avoidance usually `padding`; the accessory bar adds height |
| Safe areas | status bar + 3-button or gesture nav bar | notch/Dynamic Island + home indicator |
| Modals | Modal-on-Modal focus issues | nested Modals may fail to present |
| Dialogs | Material alert (all-caps buttons by default) | UIAlertController |
| Fonts | font_scale 1.3 ≈ large | content size `accessibility-extra-large` is much bigger |
| Push | not on emulator with many SDKs | `simctl push` works on the simulator |

## Platform gaps to name in every report

- Smoothness numbers from simulators/emulators are a regression signal only, not real-device performance.
- iOS offline is not automated (no CLI toggle); report it as not checked.
- Pinch and multi-finger gestures are not available through adb or the iOS driver.
- iOS rapid taps are ~225 ms apart (driver), so sub-100 ms races are only probed on Android.
- iOS has no "clickable" flag: a RN Pressable without `accessibilityRole` is "Other" in the hierarchy and
  escapes the small-target check; give interactive views a role (it also fixes VoiceOver).
- Push notifications and OEM background killing need a real device.
