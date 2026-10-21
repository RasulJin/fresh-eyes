# Device and Maestro facts (spike, 2026-10-19)

Machine: macOS, Xcode 26.2 (17C52), iOS runtime `com.apple.CoreSimulator.SimRuntime.iOS-26-2`, Android SDK at `~/Library/Android/sdk` (emulator, platform-tools; **no cmdline-tools / avdmanager**), Maestro CLI 2.10.0, ffmpeg from Homebrew, Python 3.9.6.

## Q7 — Android AVDs
- Only system image: `system-images/android-36.1/google_apis_playstore/arm64-v8a/`.
- No `avdmanager`, so AVDs are created by writing files directly:
  - `~/.android/avd/<name>.ini`: `avd.ini.encoding=UTF-8`, `path=<abs>/<name>.avd`, `path.rel=avd/<name>.avd`, `target=android-36.1`;
  - `~/.android/avd/<name>.avd/config.ini`: the keys used are `AvdId`, `avd.ini.displayname`, `PlayStore.enabled=true`, `abi.type=arm64-v8a`, `disk.dataPartition.size=6442450944`, `fastboot.forceColdBoot=no`, `hw.cpu.arch=arm64`, `hw.cpu.ncore=4`, `hw.ramSize=3072`, `hw.keyboard=no`, `hw.mainKeys=no`, `hw.gpu.enabled=yes`, `hw.gpu.mode=auto`, `hw.lcd.width/height/density`, `hw.initialOrientation=portrait`, `image.sysdir.1=system-images/android-36.1/google_apis_playstore/arm64-v8a/`, `skin.name=<w>x<h>`, `showDeviceFrame=no`, `tag.id=google_apis_playstore`, `target=android-36.1`.
- Created: `fresh-eyes-pixel` (1080×2400 @ 420 dpi, 2.625 px/dp) and `fresh-eyes-small` (720×1280 @ 320 dpi, 2.0 px/dp).
- Boot: `emulator -avd fresh-eyes-pixel -no-boot-anim -no-audio -port 5560 &`. It was ready (`sys.boot_completed=1`) after about 28 s. Use a fixed port per AVD (`fresh-eyes-pixel` → 5560, `fresh-eyes-small` → 5562) so the serial is predictable.
- `adb -s emulator-5560 emu avd name` prints the AVD name on its first line, which is how ownership is checked. For the user's `emulator-5554` it printed nothing, so a device whose name is empty is treated as not owned.
- Defaults on a fresh AVD: `settings get secure show_ime_with_hard_keyboard` → `0`; `settings get global window_animation_scale` → `1.0`. With `hw.keyboard=no` the soft keyboard shows anyway, but preflight still sets the flag to `1`.

## Q6 — Maestro CLI vs MCP
- The CLI binary is `~/.maestro-install/maestro/bin/maestro`. The MCP is configured in `~/.claude.json` → `projects[<path>].mcpServers.maestro` as `{command: <that binary>, args: ["mcp"]}`.
- `maestro --device <id> hierarchy` works but takes about **35 s** (JVM start plus driver install) and prints JSON in the form `{attributes:{text, accessibilityText, hintText, resource-id, bounds:"[x1,y1][x2,y2]", clickable:"false", …}, children:[…]}`. Fixture: `settings_maestro_cli.json`.
- **Critical:** running the CLI on a device the MCP is using **kills the MCP session for that device**. When the CLI exits it uninstalls the Maestro driver, and from then on every MCP call returns `DeviceServerDiedException … UNAVAILABLE` and does not recover. The only fix found is a new device serial (restart the emulator on another port) or a restart of the MCP server. **Rule: never call the Maestro CLI while the MCP drives that device.** The scripts do not use the Maestro CLI on Android.

## Q1/Q3 — hierarchy
- MCP `inspect_screen` returns compact JSON: `{"ui_schema": {"platform", "abbreviations": {b: bounds, txt: text, rid: resource-id, a11y: content-desc, hint, cls: class, scroll: scrollable, c: children}, "defaults": {...}}, "elements": [ ... ]}`. Each element has `b: "[x1,y1][x2,y2]"`, optional `txt`, `rid`, `a11y`, `cls`, `c`, and boolean flags only when they differ from the default (`clickable`, `focused`, `enabled`, …). It includes the system status bar (`com.android.systemui:*`). One Settings screen is about 5k tokens of JSON, too big to copy into files through the model.
- `adb exec-out uiautomator dump /dev/tty` takes about 5 s (up to 10 s on the first call; `--compressed` is no faster). It returns XML with `bounds="[x1,y1][x2,y2]"`, `text`, `content-desc`, `resource-id`, `class`, `package`, `clickable`, `focused`, `enabled`, `scrollable`, followed by a trailing line `UI hierchary dumped to: /dev/tty`, which must be cut after `</hierarchy>`. Fixture: `settings_uiautomator.xml`.
- **Decision:** on Android, `capture.py` takes the hierarchy with uiautomator itself, which costs no tokens. The navigator uses MCP `inspect_screen` only to navigate. `hierarchy.normalize` accepts `uiautomator`, `maestro` (MCP compact) and `maestro-cli`.

## Q2 — MCP take_screenshot
- Verified 2026-10-20 after an MCP restart: it returns the image **into the model's context** (a ~900×2000 JPEG, roughly 2k tokens) and saves a copy under the session's tool-results folder. So agents never call it; screenshots are taken by scripts (`adb exec-out screencap -p` → 1080×2400 PNG, about 210 KB; fixture `settings.png`), and the navigator never looks at images.

## Q4 — system share sheet and dialogs (Android)
- `am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT hello` opens the system Sharesheet. uiautomator sees it: 41 nodes in `package="android"`, with title text `Share` and targets such as `Quick Share`, `Copy to clipboard`.
- uiautomator dumps only the active window, so two stacked sheets look like one. The oracle for rapid taps stays "dismiss once (BACK) → the sheet must be gone".

## Q5 — rapid taps (Android)
- `input tap` is injected at the framework level, so it is invisible to `getevent` (kernel); intervals were therefore measured as the wall time of the adb commands:
  - one `adb shell input tap` takes about 520 ms (adb round trip plus `input` start);
  - three taps in sequence in one shell take about 925 ms, roughly 200 ms between taps;
  - three backgrounded taps (`input tap … & input tap … & input tap … & wait`) take about 520 ms in total, so the taps are nearly simultaneous (well under 100 ms apart);
  - `input motionevent DOWN/UP` costs about the same per command as `input tap`.
- **Decision:** `rapid_taps` on Android uses backgrounded `input tap` commands in a single `adb shell`, with `sleep <interval>` between them when `interval_ms > 0` (toybox `sleep` accepts fractions).

## Q9 — screen recording (Android)
- `screenrecord --time-limit N --bit-rate 8000000 /sdcard/fe.mp4`, then `adb pull`, then delete the file on the device.
- **The output is variable frame rate and only contains frames when the screen changes.** A static 3 s recording has one frame and duration 0. During a 300 ms swipe the recording had 10 frames over 0.67 s (about 15 fps), with the PTS starting at the first change, not at the start of the recording.
- **Consequences for `motion.py`:** work on native frames with their PTS (no `fps=` resampling, because duplicated frames hide one-frame flashes). Treat gaps between frames as rest. Wall-clock action times cannot be aligned with the video, so "never settles" is judged from whether the video runs to the end of the recording window.

## Q8 — iOS (basics; details in Task 16)
- Created `fresh-eyes iPhone 17` (`1EB9BE1B-BF5B-44BD-B1D4-CE6CCCD69C4D`) and `fresh-eyes iPhone 16e` (`B5D11571-5DD9-45C0-80AE-160584346287`) with `xcrun simctl create "<name>" com.apple.CoreSimulator.SimDeviceType.iPhone-17 com.apple.CoreSimulator.SimRuntime.iOS-26-2`.
- The Maestro MCP lists simulators by UDID as `device_id`.

## Q11 — iOS hierarchy and input (Task 16, 2026-10-21)
- `idb` is not installed, and copying MCP `inspect_screen` JSON (~5k tokens a screen) into files through the
  model is too expensive. **Decision:** reuse the Maestro XCTest driver that the MCP starts on the simulator. It
  is the `maestro-driver-iosUITests-Runner` process (its path contains the simulator UDID) serving HTTP on
  `127.0.0.1:22087`; `lsof -c maestro -iTCP -sTCP:LISTEN` + `ps` find the port for a UDID.
- Routes used (JSON bodies, coordinates in points): `GET /deviceInfo` → `{widthPoints, heightPoints, widthPixels,
  heightPixels}` (iPhone 17: 402×874 pt, 1206×2622 px); `POST /viewHierarchy {"appIds": [],
  "excludeKeyboardElements": false}` → `{"axElement": {...}, "depth"}` with `frame {X,Y,Width,Height}`,
  `elementType` (XCUIElement.ElementType raw value), `label`, `value`, `title`, `placeholderValue`,
  `identifier`, `hasFocus`, `enabled` (~48 KB, 160 nodes for Settings, 0.37 s); `POST /touch {x, y, duration}`;
  `POST /swipeV2 {startX, startY, endX, endY, duration, appIds}` (a swipe from x=2 is the back gesture);
  `POST /inputText {text, appIds}` (Cyrillic works); `POST /keyboard` (said `false` while a keyboard was up —
  not used; the Keyboard node in the hierarchy is).
- The driver serializes touches: each `/touch` takes ~225 ms, and concurrent requests fail with HTTP 500. So
  iOS rapid taps are ~225 ms apart.
- The AX tree lists some labeled elements twice (same parent, type, label, frame) and wraps rows in unlabeled
  same-frame siblings; only labeled duplicates are dropped. Single-line `StaticText` frames are padded (29 pt for
  a 17 pt font; two lines are 42 pt). Native NavigationBar/Toolbar/TabBar are translucent and content scrolls
  under them by design (iOS 26 floating toolbar).
- `simctl io recordVideo --codec=h264` gave 119 frames over 2.9 s for a push transition; `simctl ui appearance`
  and `content_size` apply within a second (`--expect-differs` confirmed both).
- Trap: the first keyboard on a fresh simulator shows the slide-to-type intro with a Continue button instead of
  keys; `DidShowContinuousPathIntroduction = 1` in `com.apple.keyboard.preferences` was already set and did not
  prevent it. One tap on Continue fixes it for good.

## Q10 — UiAutomation conflict (found 2026-10-20 while connecting the first app)
- With the Maestro MCP driver running on an emulator (`dev.mobile.maestro` process), `adb exec-out uiautomator dump`
  prints `Killed`: only one UiAutomation client is allowed.
- `am force-stop dev.mobile.maestro(.test)` frees it (the dump works again), but the MCP session for that device stays
  dead (`DeviceServerDiedException`) until the MCP server restarts.
- **Decision:** on Android, `nav.py` (uiautomator + adb input) does navigation and capture shares the same channel;
  the Maestro MCP is used only on iOS. Bonus: the navigator reads ~1.5k chars of compressed screen text instead of
  ~15k chars of MCP JSON per screen.
