#!/usr/bin/env python3
"""Write confirmed findings into a project's Markdown backlog and update entry statuses.

CLI:
  backlog.py index --file F --out OUT [--statuses open,in-progress,planned]
  backlog.py append --file F --finding finding.json [--template ru-backlog] [--date YYYY-MM-DD]
                    [--attach CROP --images-dir D]
  backlog.py set-status --file F --id BUG-12 --status verified --note TEXT [--line N] [--template ru-backlog]
"""
import argparse
import json
import os
import re
import shutil
import time

from fe_common import FEError, cli, read_json

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


def index(text, statuses=("open", "in-progress", "planned")):
    """`ID · title - where` per entry whose status is listed or missing. An id's first entry decides: later
    bullets naming it are rechecks, not entries."""
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
                break
        out.append("%s · %s" % (item_id, title) + (" - " + where if where else ""))
    return out


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
    p = sub.add_parser("append"); p.add_argument("--file", required=True)
    p.add_argument("--finding", required=True, help="path to a JSON file holding one finding")
    p.add_argument("--template", default="ru-backlog"); p.add_argument("--date")
    p.add_argument("--attach", help="crop to copy next to the backlog"); p.add_argument("--images-dir")
    p = sub.add_parser("set-status"); p.add_argument("--file", required=True); p.add_argument("--id", required=True)
    p.add_argument("--status", required=True); p.add_argument("--note", required=True)
    p.add_argument("--line", type=int); p.add_argument("--template", default="ru-backlog")
    a = ap.parse_args()
    if a.cmd == "index":
        lines = index(_read(a.file), a.statuses.split(","))
        _write(a.out, "".join(l + "\n" for l in lines))
        out = {"entries": len(lines), "out": a.out}
    elif a.cmd == "append":
        out = append(a.file, read_json(a.finding), a.template, a.date, a.attach, a.images_dir)
    else:
        set_status(a.file, a.id, a.status, a.note, a.line, a.template)
        out = {"id": a.id, "status": a.status}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
