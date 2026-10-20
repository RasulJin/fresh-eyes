#!/usr/bin/env python3
"""Check (and with --fix, repair) everything that would hide bugs or make a run unsafe, before a run starts.

Writes preflight.json: {ok, platforms:{p:{ok, device, name, owned, small_device, app_installed, soft_keyboard,
animations, problems}}, env:{name, verified, api_host}, mutating_allowed, tools:{...}, unchecked:[...]}

CLI: preflight.py --project DIR --mode quick|full [--fix] [--brief] --out preflight.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

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


def _bundle_text(platform, artifact):
    """JS bundle text of a built artifact (APK or .app), to see which API it talks to."""
    if platform == "android":
        out = subprocess.run(["unzip", "-p", artifact, "assets/index.android.bundle"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=120).stdout
        return out.decode("utf-8", "replace")
    path = os.path.join(artifact, "main.jsbundle")
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


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
    state["app_installed"] = bool(app_id) and devices.app_installed(main, app_id)
    if not state["app_installed"]:
        state["problems"].append("app %s is not installed on %s; install the build first" % (app_id, main.name))
    if platform == "android":
        if not devices.soft_keyboard_on(main) and fix:
            devices.set_soft_keyboard(main)
        state["soft_keyboard"] = devices.soft_keyboard_on(main)
        if not state["soft_keyboard"]:
            state["problems"].append("soft keyboard is off: keyboard bugs would be hidden")
        if not devices.animations_enabled(main) and fix:
            devices.enable_animations(main)
        state["animations"] = devices.animations_enabled(main)
        if not state["animations"]:
            state["problems"].append("animations are disabled: motion bugs would be hidden")
    state["ok"] = state["app_installed"] and state["soft_keyboard"] and state["animations"]
    return state


def _env(config, mode, ok_platforms, project="."):
    env = config.get("env") or {}
    expected = env.get("api_expected")
    forbidden = env.get("api_forbidden") or []
    res = {"name": env.get("name"), "verified": False, "api_host": None, "problems": []}
    if not expected or not ok_platforms:
        res["problems"].append("no api_expected in config or no ready platform")
        return res
    verified = True
    for p in ok_platforms:
        build = ((config.get("builds") or {}).get(mode) or {}).get(p) or {}
        if not build.get("artifact"):
            url = (build.get("metro_env") or {}).get("EXPO_PUBLIC_API_URL")
            good = url == expected and url not in forbidden
            if not good:
                res["problems"].append("%s quick build points at %s, expected %s" % (p, url, expected))
        else:
            artifact = build.get("artifact", "")
            if artifact and not os.path.isabs(artifact):
                artifact = os.path.join(project, artifact)
            try:
                text = _bundle_text(p, artifact)
            except (OSError, subprocess.SubprocessError) as e:
                text = ""
                res["problems"].append("%s: cannot read bundle of %s: %s" % (p, build.get("artifact"), e))
            good = expected in text and not any(f in text for f in forbidden)
            if not good and text:
                res["problems"].append("%s bundle does not point only at %s" % (p, expected))
        verified = verified and good
    res["verified"] = verified
    res["api_host"] = expected if verified else None
    return res


def check(config, mode, fix=False, project="."):
    wants_small = any("small-screen" in s.get("states", []) for s in config.get("screens", []))
    platforms = {}
    for p in config.get("platforms", ["android", "ios"]):
        platforms[p] = _platform_state(config, p, wants_small, fix)
    ok_platforms = [p for p, s in platforms.items() if s["ok"]]
    env = _env(config, mode, ok_platforms, project)
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
    a = ap.parse_args()
    try:
        res = check(read_json(os.path.join(a.project, ".fresh-eyes", "config.json")), a.mode, a.fix, a.project)
    except (FEError, OSError, ValueError) as e:
        res = {"ok": False, "mode": a.mode, "platforms": {}, "env": {"name": None, "verified": False,
                                                                     "api_host": None},
               "mutating_allowed": False, "tools": {}, "unchecked": [str(e)]}
    write_json(a.out, res)
    print(_brief(res) if a.brief else json.dumps({"ok": res["ok"], "out": a.out}))
    sys.exit(0)


if __name__ == "__main__":
    cli_main()
