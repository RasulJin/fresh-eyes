#!/usr/bin/env python3
"""Check (and with --fix, repair) everything that would hide bugs or make a run unsafe, before a run starts.

Writes preflight.json: {ok, platforms:{p:{ok, device, name, owned, small_device, app_installed, soft_keyboard,
animations, problems}}, env:{name, verified, api_host}, mutating_allowed, tools:{...}, unchecked:[...]}

CLI: preflight.py --project DIR --mode quick|full [--fix] [--brief] [--platforms android,ios] --out preflight.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

import devices
from fe_common import FEError, read_json, write_json


def _maestro_path():
    try:
        cfg = read_json(os.path.expanduser("~/.claude.json"))
    except (OSError, ValueError):
        cfg = {}
    stack = [cfg]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            cmd = cur.get("command")
            if isinstance(cmd, str) and cmd.endswith("maestro") and os.path.exists(cmd):
                return cmd
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    for cand in (shutil.which("maestro"), os.path.expanduser("~/.maestro/bin/maestro")):
        if cand and os.path.exists(cand):
            return cand
    return None


def _host(url):
    return urlparse(url).netloc or url


def _installed_bundle(platform, dev_id, app_id, cache_dir):
    """JS bundle inside the app installed on the device (release builds); None when Metro serves it."""
    if platform == "android":
        out = devices._adb(dev_id, "shell", "pm path %s" % devices._app_id(app_id), check=False)
        apk = next((l[8:].strip() for l in out.splitlines() if l.startswith("package:") and l.strip()
                    .endswith("base.apk")), None)
        if not apk:
            return None
        os.makedirs(cache_dir, exist_ok=True)
        local = os.path.join(cache_dir, "installed-android.apk")
        try:
            devices._adb(dev_id, "pull", apk, local, timeout=300)
            out = subprocess.run(["unzip", "-p", local, "assets/index.android.bundle"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=120).stdout
        finally:
            if os.path.exists(local):
                os.remove(local)  # ~100 MB per run otherwise
        return out.decode("utf-8", "replace") or None
    app = devices._simctl("get_app_container", dev_id, devices._app_id(app_id), "app", check=False).strip()
    path = os.path.join(app, "main.jsbundle") if app else ""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _metro_port(platform, dev_id):
    if platform == "android":
        m = re.search(r"tcp:\d+ tcp:(\d+)", devices._adb(dev_id, "reverse", "--list", check=False))
        if m:
            return int(m.group(1))
    return 8081


def _metro_bundle(platform, port):
    """The bundle Metro serves this platform: the Expo manifest's launchAsset, else the plain RN entry.
    Expo inlines EXPO_PUBLIC_* values here, whatever .env or the shell said."""
    base = "http://127.0.0.1:%d" % port
    url = "%s/index.bundle?platform=%s&dev=true" % (base, platform)
    try:
        req = urllib.request.Request(base + "/", headers={"expo-platform": platform,
                                                          "Accept": "application/expo+json,application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            launch = (json.loads(r.read().decode("utf-8")).get("launchAsset") or {}).get("url")
        if launch:
            u = urlparse(launch)
            url = base + u.path + ("?" + u.query if u.query else "")
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        with urllib.request.urlopen(url, timeout=600) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise FEError("no Metro bundle on port %d: %s" % (port, e))


def _prepare(dev, platform, app_id, fix):
    """One device ready to test: the app installed, nothing that hides bugs (keyboard, animations, stylus),
    and on iOS the Maestro driver up."""
    st = {"app_installed": bool(app_id) and devices.app_installed(dev, app_id), "soft_keyboard": True,
          "animations": True, "problems": []}
    if not st["app_installed"]:
        st["problems"].append("app %s is not installed on %s; install the build first" % (app_id, dev.name))
    if platform == "android":
        if not devices.soft_keyboard_on(dev) and fix:
            devices.set_soft_keyboard(dev)
        st["soft_keyboard"] = devices.soft_keyboard_on(dev)
        if not st["soft_keyboard"]:
            st["problems"].append("soft keyboard is off: keyboard bugs would be hidden")
        if not devices.animations_enabled(dev) and fix:
            devices.enable_animations(dev)
        st["animations"] = devices.animations_enabled(dev)
        if not st["animations"]:
            st["problems"].append("animations are disabled: motion bugs would be hidden")
        if devices.stylus_handwriting_on(dev) and fix:
            devices.disable_stylus_handwriting(dev)
        st["stylus_off"] = not devices.stylus_handwriting_on(dev)
        if not st["stylus_off"]:
            st["problems"].append("stylus handwriting is on: Gboard shows a stylus toolbar instead of the keyboard")
    if platform == "ios":
        try:
            devices.ios_driver(dev)
            st["driver"] = True
        except FEError as e:
            st["driver"] = False
            st["problems"].append(str(e))
    st["ok"] = st["app_installed"] and st["soft_keyboard"] and st["animations"] and \
        st.get("stylus_off", True) and st.get("driver", True)
    return st


def _platform_state(config, platform, kind_needed_small, fix):
    app_id = (config.get("app") or {}).get("%s_id" % platform)
    owned = [d for d in devices.list_devices() if d.platform == platform and devices.is_owned(d)]
    state = {"ok": False, "device": None, "name": None, "owned": False, "small_device": False,
             "app_installed": False, "soft_keyboard": True, "animations": True, "problems": []}
    main = next((d for d in owned if not d.name.endswith(("small", "16e"))), None)
    if main is None and fix:
        try:
            main = devices.ensure_owned(platform)
        except FEError as e:
            state["problems"].append(str(e))
    if main is None:
        state["problems"].append("no booted %s device named fresh-eyes*; run with --fix" % platform)
        return state
    state.update(device=main.id, name=main.name, owned=True)
    small = next((d for d in owned if d.name.endswith(("small", "16e"))), None)
    if small is None and fix and kind_needed_small:
        try:
            small = devices.ensure_owned(platform, "small")
        except FEError as e:
            state["problems"].append(str(e))
    state["small_device"] = small is not None
    if small is not None:
        state["small_device_id"] = small.id
    earlier = state["problems"]
    state.update(_prepare(main, platform, app_id, fix))
    state["problems"] = earlier + state["problems"]
    if small is not None:
        small_state = _prepare(small, platform, app_id, fix)
        if not small_state["ok"]:  # small-screen cells become "not checked", the platform stays usable
            state["small_device"] = False
            state.pop("small_device_id", None)
            state["problems"] += ["small device %s: %s" % (small.name, p) for p in small_state["problems"]]
    return state


def _env(config, mode, states, project="."):
    """Which backend the app on each ready device really talks to: the URL inside the installed bundle
    (release builds) or inside the bundle Metro serves it (dev builds). Config values alone prove nothing."""
    env = config.get("env") or {}
    expected = env.get("api_expected")
    forbidden = env.get("api_forbidden") or []
    res = {"name": env.get("name"), "verified": False, "api_host": None, "problems": []}
    ok = [p for p, st in states.items() if st["ok"]]
    if not expected or not ok:
        res["problems"].append("no api_expected in config or no ready platform")
        return res
    verified = True
    for p in ok:
        app_id = (config.get("app") or {}).get("%s_id" % p)
        try:
            text = _installed_bundle(p, states[p]["device"], app_id, os.path.join(project, ".fresh-eyes", "cache"))
            source = "installed app"
            if text is None:
                if mode == "full":
                    raise FEError("full mode needs a release build (the bundle inside the app); this one loads "
                                  "from Metro")
                port = _metro_port(p, states[p]["device"])
                text, source = _metro_bundle(p, port), "Metro :%d" % port
        except (FEError, OSError, subprocess.SubprocessError) as e:
            res["problems"].append("%s: cannot read the bundle the app runs: %s" % (p, e))
            verified = False
            continue
        # match hosts: bundlers may store the scheme and the host as separate strings
        good = _host(expected) in text and not any(_host(f) in text for f in forbidden)
        if not good:
            res["problems"].append("%s: the bundle from %s does not point only at %s" % (p, source, expected))
        verified = verified and good
    res["verified"] = verified
    res["api_host"] = expected if verified else None
    return res


def check(config, mode, fix=False, project=".", only=None):
    wants_small = any("small-screen" in s.get("states", []) for s in config.get("screens", []))
    platforms = {}
    for p in config.get("platforms", ["android", "ios"]):
        if only and p not in only:
            platforms[p] = {"ok": False, "device": None, "name": None, "owned": False, "small_device": False,
                            "app_installed": False, "soft_keyboard": True, "animations": True,
                            "problems": ["not requested in this run (--platforms)"]}
            continue
        platforms[p] = _platform_state(config, p, wants_small, fix)
    ok_platforms = [p for p, s in platforms.items() if s["ok"]]
    env = _env(config, mode, platforms, project)
    unchecked = ["%s: %s" % (p, "; ".join(s["problems"])) for p, s in platforms.items() if not s["ok"]]
    unchecked += env["problems"]
    tools = {"ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
             "adb": os.path.exists(devices.adb_path()), "xcrun": bool(shutil.which("xcrun")),
             "maestro": _maestro_path()}
    return {"ok": bool(ok_platforms) and tools["ffmpeg"] and tools["ffprobe"], "mode": mode, "platforms": platforms,
            "env": {k: env[k] for k in ("name", "verified", "api_host")},
            "mutating_allowed": bool(env["verified"] and env["name"] in ("staging", "local")),
            "tools": tools, "unchecked": unchecked}


def _brief(res):
    lines = ["preflight %s (%s)" % ("OK" if res["ok"] else "NOT READY", res["mode"])]
    for p, s in res["platforms"].items():
        lines.append("  %s: %s %s" % (p, "ready" if s["ok"] else "not ready", s.get("name") or ""))
    lines.append("  backend: %s, verified=%s, mutating=%s" % (res["env"]["name"], res["env"]["verified"],
                                                              res["mutating_allowed"]))
    missing = [k for k, v in res["tools"].items() if not v]
    if missing:
        lines.append("  missing tools: %s" % ", ".join(missing))
    for u in res["unchecked"][:15 - len(lines) - 1]:
        lines.append("  - " + u[:200])
    return "\n".join(lines[:15])


def cli_main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", required=True, choices=["quick", "full"])
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--platforms", help="comma list to check now, e.g. android")
    a = ap.parse_args()
    try:
        res = check(read_json(os.path.join(a.project, ".fresh-eyes", "config.json")), a.mode, a.fix, a.project,
                    a.platforms.split(",") if a.platforms else None)
    except (FEError, OSError, ValueError) as e:
        res = {"ok": False, "mode": a.mode, "platforms": {}, "env": {"name": None, "verified": False,
                                                                     "api_host": None},
               "mutating_allowed": False, "tools": {}, "unchecked": [str(e)]}
    write_json(a.out, res)
    print(_brief(res) if a.brief else json.dumps({"ok": res["ok"], "out": a.out}))
    sys.exit(0)


if __name__ == "__main__":
    cli_main()
