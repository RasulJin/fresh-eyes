#!/usr/bin/env python3
"""Android emulator / iOS simulator operations. Anything that changes device state refuses to run on a
device whose name does not start with "fresh-eyes" (the user's own devices are read-only to us).

CLI:
  devices.py list
  devices.py ensure --platform android|ios [--kind default|small]
  devices.py screenshot --platform P --device ID --out PNG
  devices.py set --platform P --device ID --name NAME --what dark|light|font-xl|font-default|offline|online|...
  devices.py app --platform P --device ID --name NAME --app BUNDLE --do launch|terminate|clear|background
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections import namedtuple

import fe_common
from fe_common import FEError, cli

Device = namedtuple("Device", "platform id name")
OWNED_PREFIX = "fresh-eyes"
ANDROID_OWNED = {"default": ("fresh-eyes-pixel", 5560, 1080, 2400, 420),
                 "small": ("fresh-eyes-small", 5562, 720, 1280, 320)}
IOS_OWNED = {"default": ("fresh-eyes iPhone 17", "com.apple.CoreSimulator.SimDeviceType.iPhone-17"),
             "small": ("fresh-eyes iPhone 16e", "com.apple.CoreSimulator.SimDeviceType.iPhone-16e")}
ANDROID_DATA_BYTES = {"default": 4 * 1024 ** 3, "small": 2 * 1024 ** 3}  # sparse, but the emulator wants it free
AVD_DIR = os.path.expanduser("~/.android/avd")
ANDROID_IMAGE = "system-images/android-36.1/google_apis_playstore/arm64-v8a/"
ANDROID_TARGET = "android-36.1"


def sdk_root():
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            return os.environ[env]
    return os.path.expanduser("~/Library/Android/sdk")


def adb_path():
    p = os.path.join(sdk_root(), "platform-tools", "adb")
    return p if os.path.exists(p) else (shutil.which("adb") or "adb")


def _adb(dev_id, *args, timeout=60, check=True):
    cmd = [adb_path()] + (["-s", dev_id] if dev_id else []) + list(args)
    return fe_common.run(cmd, timeout=timeout, check=check).stdout.decode("utf-8", "replace")


def _shell(dev, command, timeout=60, check=True):
    return _adb(dev.id, "shell", command, timeout=timeout, check=check)


def _simctl(*args, timeout=120, check=True):
    return fe_common.run(["xcrun", "simctl"] + list(args), timeout=timeout, check=check).stdout.decode("utf-8",
                                                                                                         "replace")


def is_owned(dev):
    return bool(dev.name) and dev.name.startswith(OWNED_PREFIX)


def _require_owned(dev, action):
    if not is_owned(dev):
        raise FEError("refusing to %s on %s: not a %s device" % (action, dev.name or dev.id, OWNED_PREFIX))


def list_devices():
    found = []
    try:
        out = _adb(None, "devices", check=False)
    except FEError:
        out = ""
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            name = ""
            if parts[0].startswith("emulator-"):
                name = (_adb(parts[0], "emu", "avd", "name", check=False).splitlines() or [""])[0].strip()
            found.append(Device("android", parts[0], name))
    try:
        sims = json.loads(_simctl("list", "devices", "booted", "-j", check=False) or "{}")
    except (FEError, ValueError):
        sims = {}
    for runtime_devices in (sims.get("devices") or {}).values():
        for d in runtime_devices:
            if d.get("state") == "Booted":
                found.append(Device("ios", d["udid"], d["name"]))
    return found


def _write_avd(name, w, h, dens, data_bytes):
    avd_dir = AVD_DIR
    os.makedirs(os.path.join(avd_dir, name + ".avd"), exist_ok=True)
    with open(os.path.join(avd_dir, name + ".ini"), "w") as f:
        f.write("avd.ini.encoding=UTF-8\npath=%s/%s.avd\npath.rel=avd/%s.avd\ntarget=%s\n"
                % (avd_dir, name, name, ANDROID_TARGET))
    cfg = {"AvdId": name, "avd.ini.displayname": name, "PlayStore.enabled": "true", "abi.type": "arm64-v8a",
           "disk.dataPartition.size": str(data_bytes), "fastboot.forceColdBoot": "no", "hw.cpu.arch": "arm64",
           "hw.cpu.ncore": "4", "hw.ramSize": "3072", "hw.keyboard": "no", "hw.mainKeys": "no", "hw.dPad": "no",
           "hw.trackBall": "no", "hw.gpu.enabled": "yes", "hw.gpu.mode": "auto", "hw.lcd.width": str(w),
           "hw.lcd.height": str(h), "hw.lcd.density": str(dens), "hw.initialOrientation": "portrait",
           "hw.sdCard": "yes", "image.sysdir.1": ANDROID_IMAGE, "skin.dynamic": "yes",
           "skin.name": "%dx%d" % (w, h), "showDeviceFrame": "no", "tag.display": "Google Play",
           "tag.id": "google_apis_playstore", "target": ANDROID_TARGET, "vm.heapSize": "256"}
    with open(os.path.join(avd_dir, name + ".avd", "config.ini"), "w") as f:
        f.write("".join("%s=%s\n" % kv for kv in cfg.items()))


def _open_log(name):
    return open(os.path.join(AVD_DIR, name + ".fresh-eyes.log"), "w")


def _log_tail(name):
    try:
        with open(os.path.join(AVD_DIR, name + ".fresh-eyes.log"), errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
    except OSError:
        return "no log"
    fatal = [l for l in lines if "FATAL" in l or "ERROR" in l]
    return (fatal or lines or ["no output"])[-1][:300]


def ensure_owned(platform, kind="default", boot_timeout=240):
    """Create (if missing) and boot one of our own devices; return it."""
    if platform == "android":
        name, port, w, h, dens = ANDROID_OWNED[kind]
        serial = "emulator-%d" % port
        proc = None
        if not any(d.id == serial for d in list_devices()):
            if not os.path.exists(os.path.join(AVD_DIR, name + ".ini")):
                _write_avd(name, w, h, dens, ANDROID_DATA_BYTES[kind])
            log = _open_log(name)
            proc = subprocess.Popen([os.path.join(sdk_root(), "emulator", "emulator"), "-avd", name,
                                     "-no-boot-anim", "-no-audio", "-port", str(port)],
                                    stdout=log, stderr=log, start_new_session=True)
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if proc is not None and proc.poll() is not None:
                raise FEError("%s exited while booting: %s" % (name, _log_tail(name)))
            if _adb(serial, "shell", "getprop", "sys.boot_completed", check=False).strip() == "1":
                return Device("android", serial, name)
            time.sleep(3)
        raise FEError("%s did not boot within %ds" % (name, boot_timeout))
    if platform == "ios":
        name, devtype = IOS_OWNED[kind]
        sims = json.loads(_simctl("list", "devices", "-j"))
        udid = None
        for devs in sims["devices"].values():
            for d in devs:
                if d["name"] == name and d.get("isAvailable", True):
                    udid = d["udid"]
        if udid is None:
            runtimes = [r for r in json.loads(_simctl("list", "runtimes", "-j"))["runtimes"]
                        if r.get("isAvailable") and r["identifier"].split(".")[-1].startswith("iOS")]
            if not runtimes:
                raise FEError("no available iOS simulator runtime")
            udid = _simctl("create", name, devtype, runtimes[-1]["identifier"]).strip()
        _simctl("boot", udid, check=False)
        _simctl("bootstatus", udid, "-b", timeout=boot_timeout)
        return Device("ios", udid, name)
    raise FEError("unknown platform %s" % platform)


def screenshot(dev, dst):
    if dev.platform == "android":
        data = fe_common.run([adb_path(), "-s", dev.id, "exec-out", "screencap", "-p"]).stdout
        if not data.startswith(b"\x89PNG"):
            raise FEError("screencap returned no PNG for %s" % dev.id)
        with open(dst, "wb") as f:
            f.write(data)
    else:
        _simctl("io", dev.id, "screenshot", dst)
    return dst


class Recording(object):
    def __init__(self, dev, dst, proc, remote=None):
        self.dev, self.dst, self.proc, self.remote = dev, dst, proc, remote
        self.started = time.time()


def start_recording(dev, dst, limit_s=60):
    if dev.platform == "android":
        remote = "/sdcard/fe_rec.mp4"
        proc = subprocess.Popen([adb_path(), "-s", dev.id, "shell", "screenrecord", "--bit-rate", "8000000",
                                 "--time-limit", str(limit_s), remote], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        return Recording(dev, dst, proc, remote)
    proc = subprocess.Popen(["xcrun", "simctl", "io", dev.id, "recordVideo", "--codec=h264", "--force", dst],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 10
    buf = b""
    while time.time() < deadline and b"Recording started" not in buf:
        chunk = proc.stderr.read1(256) if hasattr(proc.stderr, "read1") else proc.stderr.read(64)
        if not chunk:
            break
        buf += chunk
    if b"Recording started" not in buf:
        proc.kill()
        raise FEError("simctl recordVideo did not start: %s" % buf.decode("utf-8", "replace")[-200:])
    return Recording(dev, dst, proc)


def stop_recording(rec):
    if rec.dev.platform == "android":
        _shell(rec.dev, "pkill -INT screenrecord", check=False)
        try:
            rec.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rec.proc.kill()
        time.sleep(0.5)
        _adb(rec.dev.id, "pull", rec.remote, rec.dst)
        _shell(rec.dev, "rm -f %s" % rec.remote, check=False)
    else:
        rec.proc.send_signal(signal.SIGINT)
        try:
            rec.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            rec.proc.kill()
    if not os.path.exists(rec.dst) or os.path.getsize(rec.dst) == 0:
        raise FEError("recording produced no file at %s" % rec.dst)
    return rec.dst


def _ios_input_unsupported(what):
    raise FEError("%s on iOS goes through Maestro flows (see docs/spikes/device-facts.md)" % what)


def tap(dev, x, y):
    if dev.platform != "android":
        _ios_input_unsupported("tap")
    _shell(dev, "input tap %d %d" % (x, y))


def rapid_taps(dev, x, y, count, interval_ms):
    """All taps in one adb shell, backgrounded so they land nearly at once (sequential taps are ~200 ms apart)."""
    if dev.platform != "android":
        _ios_input_unsupported("rapid taps")
    sep = " sleep %g; " % (interval_ms / 1000.0) if interval_ms > 0 else " "
    _shell(dev, sep.join(["input tap %d %d &" % (x, y)] * count) + " wait")


def back(dev, count=1, interval_ms=0):
    if dev.platform != "android":
        _ios_input_unsupported("back")
    sep = " sleep %g; " % (interval_ms / 1000.0) if interval_ms > 0 else "; "
    _shell(dev, sep.join(["input keyevent 4"] * count))


def launch(dev, app_id):
    if dev.platform == "android":
        lines = _shell(dev, "cmd package resolve-activity --brief %s" % app_id, check=False).strip().splitlines()
        component = lines[-1].strip() if lines and "/" in lines[-1] else ""
        if component:
            _shell(dev, "am start -n %s" % component)
        else:  # monkey exits non-zero on some images even when it launched the app
            _shell(dev, "monkey -p %s -c android.intent.category.LAUNCHER 1" % app_id, check=False)
    else:
        _simctl("launch", dev.id, app_id)


def terminate(dev, app_id):
    if dev.platform == "android":
        _shell(dev, "am force-stop %s" % app_id)
    else:
        _simctl("terminate", dev.id, app_id, check=False)


def background_foreground(dev, app_id, seconds=3):
    if dev.platform == "android":
        _shell(dev, "input keyevent 3")
    else:
        _simctl("launch", dev.id, "com.apple.Preferences")
    time.sleep(seconds)
    launch(dev, app_id)


def app_installed(dev, app_id):
    if dev.platform == "android":
        return "package:" in _shell(dev, "pm path %s" % app_id, check=False)
    return bool(_simctl("get_app_container", dev.id, app_id, check=False).strip())


def install(dev, artifact):
    if dev.platform == "android":
        _adb(dev.id, "install", "-r", artifact, timeout=300)
    else:
        _simctl("install", dev.id, artifact, timeout=300)


def clear_app(dev, app_id):
    _require_owned(dev, "clear app data")
    if dev.platform == "android":
        _shell(dev, "pm clear %s" % app_id)
    else:
        _simctl("uninstall", dev.id, app_id)


def set_dark(dev, on):
    _require_owned(dev, "change appearance")
    if dev.platform == "android":
        _shell(dev, "cmd uimode night %s" % ("yes" if on else "no"))
    else:
        _simctl("ui", dev.id, "appearance", "dark" if on else "light")


def set_font_scale(dev, level):
    _require_owned(dev, "change font size")
    if dev.platform == "android":
        _shell(dev, "settings put system font_scale %s" % ("1.3" if level == "xl" else "1.0"))
    else:
        _simctl("ui", dev.id, "content_size", "accessibility-extra-large" if level == "xl" else "large")


def set_network(dev, online):
    _require_owned(dev, "change network")
    if dev.platform != "android":
        raise FEError("network toggling is unsupported on ios")
    state = "enable" if online else "disable"
    _shell(dev, "svc wifi %s; svc data %s" % (state, state))


def soft_keyboard_on(dev):
    if dev.platform != "android":
        return True
    return _shell(dev, "settings get secure show_ime_with_hard_keyboard", check=False).strip() == "1"


def set_soft_keyboard(dev):
    _require_owned(dev, "change keyboard settings")
    _shell(dev, "settings put secure show_ime_with_hard_keyboard 1")


_SCALES = ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")


def animations_enabled(dev):
    if dev.platform != "android":
        return True
    for key in _SCALES:
        val = _shell(dev, "settings get global %s" % key, check=False).strip()
        if val not in ("", "null") and float(val) == 0.0:
            return False
    return True


def enable_animations(dev):
    _require_owned(dev, "change animation settings")
    _shell(dev, "; ".join("settings put global %s 1" % k for k in _SCALES))


def keyboard_top(dev):
    if dev.platform != "android":
        return None
    if "mInputShown=true" not in _shell(dev, "dumpsys input_method", check=False):
        return None
    win = _shell(dev, "dumpsys window InputMethod", check=False)
    m = re.search(r"touchable region=SkRegion\(\(\d+,(\d+),\d+,\d+\)", win) or \
        re.search(r"mFrame=\[\d+,(\d+)\]\[\d+,\d+\]", win)
    return int(m.group(1)) if m else None


GBOARD = "com.google.android.inputmethod.latin"


def stylus_handwriting_on(dev):
    """Emulators report a STYLUS input source; with handwriting on, Gboard shows a stylus onboarding and a small
    toolbar instead of the keyboard, which hides every keyboard bug."""
    if dev.platform != "android":
        return False
    return _shell(dev, "settings get secure stylus_handwriting_enabled", check=False).strip() != "0"


def disable_stylus_handwriting(dev):
    _require_owned(dev, "change stylus settings")
    _shell(dev, "settings put secure stylus_handwriting_enabled 0")
    _shell(dev, "am force-stop %s" % GBOARD, check=False)


def px_per_dp(dev):
    if dev.platform != "android":
        return 3.0
    out = _shell(dev, "wm density", check=False)
    m = re.search(r"Override density: (\d+)", out) or re.search(r"Physical density: (\d+)", out)
    return int(m.group(1)) / 160.0 if m else 2.625


def screen_size(dev):
    if dev.platform == "android":
        out = _shell(dev, "wm size", check=False)
        m = re.search(r"Override size: (\d+)x(\d+)", out) or re.search(r"Physical size: (\d+)x(\d+)", out)
        if m:
            return [int(m.group(1)), int(m.group(2))]
    return None


SETTINGS = {
    "dark": lambda d: set_dark(d, True), "light": lambda d: set_dark(d, False),
    "font-xl": lambda d: set_font_scale(d, "xl"), "font-default": lambda d: set_font_scale(d, "default"),
    "offline": lambda d: set_network(d, False), "online": lambda d: set_network(d, True),
    "soft-keyboard": set_soft_keyboard, "animations-on": enable_animations,
}


def apply_setting(dev, what):
    if what not in SETTINGS:
        raise FEError("unknown setting %s (known: %s)" % (what, ", ".join(sorted(SETTINGS))))
    _require_owned(dev, "apply %s" % what)
    SETTINGS[what](dev)


APP_ACTIONS = {"launch": launch, "terminate": terminate, "clear": clear_app,
               "background": background_foreground}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("ensure"); p.add_argument("--platform", required=True, choices=["android", "ios"])
    p.add_argument("--kind", default="default", choices=["default", "small"])
    p = sub.add_parser("screenshot"); p.add_argument("--platform", required=True); p.add_argument("--device",
                                                                                                required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("set"); p.add_argument("--platform", required=True); p.add_argument("--device", required=True)
    p.add_argument("--name", required=True); p.add_argument("--what", required=True, choices=sorted(SETTINGS))
    p = sub.add_parser("app"); p.add_argument("--platform", required=True); p.add_argument("--device", required=True)
    p.add_argument("--name", required=True); p.add_argument("--app", required=True)
    p.add_argument("--do", required=True, choices=sorted(APP_ACTIONS))
    a = ap.parse_args()
    if a.cmd == "list":
        out = [dict(d._asdict(), owned=is_owned(d)) for d in list_devices()]
    elif a.cmd == "ensure":
        out = ensure_owned(a.platform, a.kind)._asdict()
    elif a.cmd == "set":
        apply_setting(Device(a.platform, a.device, a.name), a.what)
        out = {"set": a.what}
    elif a.cmd == "app":
        APP_ACTIONS[a.do](Device(a.platform, a.device, a.name), a.app)
        out = {"app": a.app, "do": a.do}
    else:
        out = screenshot(Device(a.platform, a.device, ""), a.out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
