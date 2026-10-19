#!/usr/bin/env python3
"""Write confirmed findings into a project's Markdown backlog and update entry statuses.

CLI:
  backlog.py next-id --file F --prefix BUG|IMP
  backlog.py append --file F --finding finding.json [--template ru-backlog] [--date YYYY-MM-DD]
  backlog.py set-status --file F --id BUG-12 --status verified --note TEXT [--line N] [--template ru-backlog]
  backlog.py attach --src PNG --images-dir D --id BUG-54 --slug TEXT --relative-to DIR
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
                  "- **Шаги** — {steps}\n"
                  "- **Ожидание / Факт**\n"
                  "  - Ожидание: {expected}\n"
                  "  - Факт: {actual}\n"
                  "- **Заметки** — уверенность {confidence}; правило: {rule}; доказательство: {evidence}\n"),
        "recheck": "- **Перепроверка fresh-eyes** — {note}",
    },
    "en-backlog": {
        "entry": ("### {id} · {title} — `open`\n"
                  "- **Where** — {screen}, {platform}, {date} (fresh-eyes)\n"
                  "- **Steps** — {steps}\n"
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
        steps=" → ".join(steps) if isinstance(steps, list) else steps,
        expected=finding.get("expected", ""), actual=finding.get("actual", ""),
        confidence=finding.get("confidence", ""), rule=finding.get("rule", ""),
        evidence=", ".join(evidence) if isinstance(evidence, list) else evidence)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path, finding, template, date=None):
    text = _read(path) if os.path.exists(path) else ""
    prefix = "IMP" if finding.get("kind") == "IMP" else "BUG"
    item_id = "%s-%d" % (prefix, next_id(text, prefix))
    entry = render(finding, template, item_id, date or time.strftime("%Y-%m-%d"))
    _write(path, text.rstrip("\n") + "\n\n" + entry)
    return item_id


def set_status(path, item_id, status, note, line=None, template="ru-backlog"):
    lines = _read(path).split("\n")
    head = re.compile(r"^(?:#{2,4} %s(?=[ ·—])|- \*\*%s\*\*)" % (re.escape(item_id), re.escape(item_id)))
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


def attach(src, images_dir, item_id, slug, relative_to):
    os.makedirs(images_dir, exist_ok=True)
    clean = re.sub(r"[^\w]+", "-", slug.lower()).strip("-")[:40] or "evidence"
    dst = os.path.join(images_dir, "%s-%s%s" % (item_id.lower(), clean, os.path.splitext(src)[1] or ".png"))
    shutil.copyfile(src, dst)
    return os.path.relpath(dst, relative_to)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("next-id"); p.add_argument("--file", required=True); p.add_argument("--prefix", required=True)
    p = sub.add_parser("append"); p.add_argument("--file", required=True); p.add_argument("--finding", required=True)
    p.add_argument("--template", default="ru-backlog"); p.add_argument("--date")
    p = sub.add_parser("set-status"); p.add_argument("--file", required=True); p.add_argument("--id", required=True)
    p.add_argument("--status", required=True); p.add_argument("--note", required=True)
    p.add_argument("--line", type=int); p.add_argument("--template", default="ru-backlog")
    p = sub.add_parser("attach"); p.add_argument("--src", required=True); p.add_argument("--images-dir", required=True)
    p.add_argument("--id", required=True); p.add_argument("--slug", required=True)
    p.add_argument("--relative-to", required=True)
    a = ap.parse_args()
    if a.cmd == "next-id":
        out = next_id(_read(a.file), a.prefix)
    elif a.cmd == "append":
        out = append(a.file, read_json(a.finding), a.template, a.date)
    elif a.cmd == "set-status":
        set_status(a.file, a.id, a.status, a.note, a.line, a.template)
        out = {"id": a.id, "status": a.status}
    else:
        out = attach(a.src, a.images_dir, a.id, a.slug, a.relative_to)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
