# Platform notes and test-bench traps

## Devices

- fresh-eyes only changes devices it owns: Android AVDs `fresh-eyes-pixel` (port 5560) and `fresh-eyes-small`
  (port 5562), iOS simulators `fresh-eyes iPhone 17` and `fresh-eyes iPhone 16e`. `devices.py ensure` creates
  and boots them. Other devices (the user's emulator, a phone) are read-only to us.
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
- **Animations disabled** (`animator_duration_scale=0`, Maestro `disableAnimations`) hides motion bugs.
  Preflight restores scale 1 on own devices.
- **Maestro waits for the screen to settle** before every step and after taps. That masks races; fire fast
  probes with `taps.py` / adb instead.
- **Stock emulators keep sockets alive in background**; real phones from aggressive OEMs kill them. Toggle the
  network during background probes to imitate them.
- **Wrong backend**: local builds often read a production URL from `.env`. Preflight verifies the URL the
  installed build really uses; without that, mutating probes are off.
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
- Pinch and multi-finger gestures are not available through adb.
- Push notifications and OEM background killing need a real device.
