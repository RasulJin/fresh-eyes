#!/usr/bin/env python3
"""Write confirmed findings into a project's Markdown backlog and update entry statuses.

CLI:
  backlog.py index --file F --out OUT [--statuses open,in-progress,planned] [--ref COMMIT] [--config C]
                    (config backlog.extra: open tasks kept elsewhere, a file or {"cmd": ...} printing `<id>\t<title>`
                    lines; they join the index and match like entries naming no screen)
  backlog.py append --file F --finding finding.json [--template ru-backlog] [--date YYYY-MM-DD]
                    [--attach CROP --images-dir D]
  backlog.py set-status --file F --id BUG-12 --status verified --note TEXT [--line N] [--template ru-backlog]
  backlog.py match --index backlog-index.md --findings F [F ...] [--out imp.md] [--language ru|en]
"""
import argparse
import json
import os
import re
import shutil
import time

from fe_common import FEError, cli, read_json, run, write_json

TEMPLATES = {
    "ru-backlog": {
        "entry": ("### {id} · {title} — `open`\n"
                  "- **Где** — {screen}, {platform}, {date} (fresh-eyes)\n"
                  "- **Шаги**\n{steps}\n"
                  "- **Ожидание / Факт**\n"
                  "  - Ожидание: {expected}\n"
                  "  - Факт: {actual}\n"
                  "- **Заметки** — уверенность {confidence}; правило: {rule}; доказательство: {evidence}\n"),
        "recheck": "- **Перепроверка fresh-eyes** — {note}",
    },
    "en-backlog": {
        "entry": ("### {id} · {title} — `open`\n"
                  "- **Where** — {screen}, {platform}, {date} (fresh-eyes)\n"
                  "- **Steps**\n{steps}\n"
                  "- **Expected / Actual**\n"
                  "  - Expected: {expected}\n"
                  "  - Actual: {actual}\n"
                  "- **Notes** — confidence {confidence}; rule: {rule}; evidence: {evidence}\n"),
        "recheck": "- **fresh-eyes recheck** — {note}",
    },
}
KNOWN_STATUSES = ("open", "in-progress", "fixed", "verified", "planned", "wontfix")


def _template(name):
    if name not in TEMPLATES:
        raise FEError("unknown backlog template %s (known: %s)" % (name, ", ".join(sorted(TEMPLATES))))
    return TEMPLATES[name]


def next_id(text, prefix):
    """One past the highest PREFIX-N mentioned anywhere: entries live in headings, bullets and tables."""
    nums = [int(m) for m in re.findall(r"\b%s-(\d+)\b" % re.escape(prefix), text)]
    return max(nums) + 1 if nums else 1


def render(finding, template, item_id, date):
    steps = finding.get("steps") or []
    evidence = finding.get("evidence") or []
    return _template(template)["entry"].format(
        id=item_id, title=finding.get("title", ""), screen=finding.get("screen", ""),
        platform=finding.get("platform", ""), date=date,
        steps="\n".join("  %d. %s" % (i, s) for i, s in enumerate(steps if isinstance(steps, list) else [steps], 1)),
        expected=finding.get("expected", ""), actual=finding.get("actual", ""),
        confidence=finding.get("confidence", ""), rule=finding.get("rule", ""),
        evidence=", ".join(evidence) if isinstance(evidence, list) else evidence)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path, finding, template, date=None, attach_src=None, images_dir=None):
    """Add an entry with the next id; with attach_src, copy that crop into images_dir under the new id and link
    it from the entry (run folders are git-ignored, so evidence must live next to the backlog)."""
    text = _read(path) if os.path.exists(path) else ""
    prefix = "IMP" if finding.get("kind") == "IMP" else "BUG"
    item_id = "%s-%d" % (prefix, next_id(text, prefix))
    if attach_src:
        if not images_dir:
            raise FEError("--attach needs --images-dir")
        rel = attach(attach_src, images_dir, item_id, finding.get("title", ""),
                     os.path.dirname(os.path.abspath(path)))
        finding = dict(finding, evidence=[rel])
    entry = render(finding, template, item_id, date or time.strftime("%Y-%m-%d"))
    _write(path, text.rstrip("\n") + "\n\n" + entry)
    return item_id


def _head(id_re):
    """An entry starts at a `##`-`####` heading or a `- **ID**` bullet naming the id."""
    return re.compile(r"^(?:#{2,4} (%s)(?=[ ·—])|- \*\*(%s)\*\*)" % (id_re, id_re))


STATUS = re.compile(r"— `([^`\s]+)")  # the first `word` after a dash: `fixed (abc on main)` reads fixed
WHERE = re.compile(r"^\s*- \*\*(?:Где|Where)\*\*\s*[—:-]*\s*(.*)")


def index(text, statuses=("open", "in-progress", "planned"), names=None):
    """`ID · title - where` per entry whose status is listed or missing. An id's first entry decides: later
    bullets naming it are rechecks, not entries. names: config screen name -> id, so `where` names the screen."""
    lines, out, seen = text.split("\n"), [], set()
    head = _head(r"(?:BUG|IMP)-\d+")
    for i, line in enumerate(lines):
        m = head.match(line)
        if not m or (m.group(1) or m.group(2)) in seen:
            continue
        item_id = m.group(1) or m.group(2)
        seen.add(item_id)
        rest = line[m.end():]
        st = STATUS.search(rest)
        if st and st.group(1).lower() not in statuses:
            continue
        title = (rest[:st.start()] if st else rest).strip(" ·—")
        where = ""
        for body in lines[i + 1:]:
            if body.startswith("#") or head.match(body):
                break
            w = WHERE.match(body)
            if w:
                where = w.group(1).split(",")[0].strip()
                where = (names or {}).get(where, where)
                break
        out.append("%s · %s" % (item_id, title) + (" - " + where if where else ""))
    return out


def read_at(path, ref=None):
    """The backlog file, or with ref its text in that commit: a checkout on another branch has an older backlog."""
    if not ref:
        return _read(path)
    folder, name = os.path.split(os.path.abspath(path))
    return run(["git", "-C", folder, "show", "%s:./%s" % (ref, name)]).stdout.decode("utf-8")


def set_status(path, item_id, status, note, line=None, template="ru-backlog"):
    if status not in KNOWN_STATUSES:
        raise FEError("status must be one of %s" % ", ".join(KNOWN_STATUSES))
    lines = _read(path).split("\n")
    head = _head(re.escape(item_id))
    hits = [i + 1 for i, l in enumerate(lines) if head.match(l)]
    if not hits:
        raise FEError("%s not found in %s" % (item_id, path))
    if line is None:
        if len(hits) > 1:
            raise FEError("%s appears on lines %s; pass --line" % (item_id, ", ".join(map(str, hits))))
        line = hits[0]
    elif line not in hits:
        raise FEError("line %d is not a %s heading (headings on lines %s)" % (line, item_id,
                                                                             ", ".join(map(str, hits))))
    i = line - 1
    heading = lines[i]
    m = re.search(r"`(%s)`" % "|".join(re.escape(s) for s in KNOWN_STATUSES), heading)
    if m:
        heading = heading[:m.start()] + "`%s`" % status + heading[m.end():]
    else:
        heading += " — `%s`" % status
    lines[i] = heading
    lines.insert(i + 1, _template(template)["recheck"].format(note=note))
    _write(path, "\n".join(lines))


STOP = set("это или как что для при без над под уже все его она они так там тут еще только".split())
IMP_HEADS = {"ru": ("Новые улучшения", "Уже в бэклоге"), "en": ("New improvements", "Already in the backlog")}


def _stems(text, n=5):
    """First n letters of each word of 3+ letters: «прилёта» and «прилёт» meet, endings do not matter."""
    return {w[:n] for w in re.findall(r"[a-zа-я]{3,}", text.lower().replace("ё", "е")) if w not in STOP}


def entries(lines, prefix):
    """(id, title, screen) of the PREFIX lines `index` wrote (None: backlog.extra's, any other id); screen when the
    line ends in ` - <screen-id>`."""
    out = []
    for line in lines:
        item_id, _, rest = line.strip().partition(" · ")
        ours = item_id.startswith(prefix + "-") if prefix else bool(rest) and not item_id.startswith(("BUG-", "IMP-"))
        if ours:
            title, _, where = rest.rpartition(" - ")
            if not (title and re.fullmatch(r"[a-z0-9][a-z0-9-]*", where)):
                title, where = rest, None
            out.append((item_id, title, where))
    return out


def extra(spec):
    """Index lines `<id> · <title>` of config backlog.extra: a file, or {"cmd": ...}, of `<id>\t<title>` lines."""
    text = run(["sh", "-c", spec["cmd"]], timeout=120).stdout.decode("utf-8") if isinstance(spec, dict) \
        else _read(spec)
    return ["%s · %s" % (i.strip(), " ".join(t.split())) for i, _, t in (l.partition("\t") for l in text.splitlines())
            if i.strip() and t.strip()]


def match_entry(finding, entries, near=2, size=5):
    """(id, title) of the open entry that already holds this finding: the most shared stems (`size` letters), at
    least half of the shorter title's, `near` on the same screen and 3 for an entry naming none; an entry on another
    screen never."""
    mine, best = _stems(finding.get("title", ""), size), None
    for item_id, title, where in entries:
        n = len(mine & _stems(title, size))
        if (not where or where == finding.get("screen")) and n >= (near if where else 3) \
                and n >= min(len(mine), len(_stems(title, size))) / 2 and (best is None or n > best[0]):
            best = (n, item_id, title)
    return best[1:] if best else None


def match(index_lines, paths, out=None, language="en"):
    """Give each IMP without backlog_id the open entry that already proposes it, and each pending BUG the open BUG
    entry on its screen sharing 3 stems (before the verifier, which keeps it only for the same defect: no question
    to the owner about a bug already in the backlog); findings files written back. With out, write imp.md: the
    new IMPs in full, the known ones a line each. The report lists only the new ones."""
    other = entries(index_lines, None)  # backlog.extra: either kind
    imps, bugs = entries(index_lines, "IMP") + other, entries(index_lines, "BUG") + other
    titles = {e[0]: e[1] for e in imps}
    new, known, matched, bug_hits = [], [], 0, 0
    for p in paths:
        data = read_json(p)
        asked = {q.get("finding"): q.get("text", "") for q in data.get("questions") or [] if isinstance(q, dict)}
        hits = 0
        for f in data.get("findings", []):
            if f.get("kind", "BUG") == "BUG":
                # 4 letters: «Ветке» meets «Ветка»; a verifier keeps the id only for the same defect
                hit = None if f.get("backlog_id") or f.get("verdict", "pending") != "pending" \
                    else match_entry(f, bugs, 3, 4)
                if hit:
                    f["backlog_id"], hits, bug_hits = hit[0], hits + 1, bug_hits + 1
                continue
            if f.get("kind") != "IMP" or f.get("verdict") == "rejected":
                continue
            hit = None if f.get("backlog_id") else match_entry(f, imps)
            if hit:
                f["backlog_id"], hits, matched = hit[0], hits + 1, matched + 1
            if out:
                ref = "%s#%s" % (os.path.relpath(p, os.path.dirname(os.path.abspath(out))), f.get("id"))
                (known if f.get("backlog_id") else new).append((f, ref, asked.get(f.get("id"))))
        if hits:
            write_json(p, data)
    if not out:
        return {"matched": matched, "bugs": bug_hits}
    heads = IMP_HEADS.get(language, IMP_HEADS["en"])
    lines = ["# %s: %d" % (heads[0], len(new)), ""]
    for k, (f, ref, question) in enumerate(new, 1):
        lines += ["## %d. %s" % (k, f.get("title", "")),
                  "%s · %s · %s · `%s`" % (f.get("screen"), f.get("platform"), f.get("severity"), ref)]
        lines += [x for x in (f.get("actual"), f.get("expected") and "→ " + f["expected"], f.get("verify_note"),
                              question and "? " + question) if x]
        lines += ["`%s`" % e for e in (f.get("evidence") or [])[:1]] + [""]
    lines += ["# %s: %d" % (heads[1], len(known)), ""]
    lines += ["- %s — %s%s · `%s`" % (f.get("title", ""), f["backlog_id"],
                                      " «%s»" % titles[f["backlog_id"]] if f["backlog_id"] in titles else "", ref)
              for f, ref, _ in known]
    _write(out, "\n".join(lines) + "\n")
    return {"new": len(new), "known": len(known), "matched": matched, "bugs": bug_hits, "out": out}


# Image names stay Latin: git quotes non-ASCII paths, and the rest of a backlog's images are Latin.
RU_LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def attach(src, images_dir, item_id, slug, relative_to):
    os.makedirs(images_dir, exist_ok=True)
    latin = "".join(RU_LATIN.get(ch, ch) for ch in slug.lower())
    clean = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:40].strip("-") or "evidence"
    dst = os.path.join(images_dir, "%s-%s%s" % (item_id.lower(), clean, os.path.splitext(src)[1] or ".png"))
    shutil.copyfile(src, dst)
    return os.path.relpath(dst, relative_to)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="open entries, one line each, for the verifier's dedup")
    p.add_argument("--file", required=True); p.add_argument("--out", required=True)
    p.add_argument("--statuses", default="open,in-progress,planned", help="comma list; an entry without one counts")
    p.add_argument("--ref", help="read the backlog as of this commit (the build's), not the checkout's file")
    p.add_argument("--config", help=".fresh-eyes/config.json: a `Где` naming a screen becomes its id")
    p = sub.add_parser("append"); p.add_argument("--file", required=True)
    p.add_argument("--finding", required=True, help="path to a JSON file holding one finding")
    p.add_argument("--template", default="ru-backlog"); p.add_argument("--date")
    p.add_argument("--attach", help="crop to copy next to the backlog"); p.add_argument("--images-dir")
    p = sub.add_parser("set-status"); p.add_argument("--file", required=True); p.add_argument("--id", required=True)
    p.add_argument("--status", required=True); p.add_argument("--note", required=True)
    p.add_argument("--line", type=int); p.add_argument("--template", default="ru-backlog")
    p = sub.add_parser("match", help="IMPs already in the backlog get its id; imp.md holds every IMP in full")
    p.add_argument("--index", required=True, help="backlog-index.md (a missing file: nothing is known)")
    p.add_argument("--findings", nargs="+", required=True); p.add_argument("--out", help="imp.md")
    p.add_argument("--language", default="en")
    a = ap.parse_args()
    if a.cmd == "index":
        names = {s["name"]: s["id"] for s in read_json(a.config).get("screens", []) if s.get("name")} \
            if a.config else None
        lines = index(read_at(a.file, a.ref), a.statuses.split(","), names)
        more = ((read_json(a.config).get("backlog") or {}).get("extra") if a.config else None)
        out = {}
        try:
            lines += extra(more) if more else []
        except (FEError, OSError, KeyError) as e:  # the backlog still indexes; the reply says what is missing
            out["extra_error"] = "backlog.extra not read: %s" % e
        _write(a.out, "".join(l + "\n" for l in lines))
        out.update(entries=len(lines), out=a.out)
    elif a.cmd == "match":
        lines = _read(a.index).splitlines() if os.path.exists(a.index) else []
        out = match(lines, a.findings, a.out, a.language)
    elif a.cmd == "append":
        out = append(a.file, read_json(a.finding), a.template, a.date, a.attach, a.images_dir)
    else:
        set_status(a.file, a.id, a.status, a.note, a.line, a.template)
        out = {"id": a.id, "status": a.status}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
