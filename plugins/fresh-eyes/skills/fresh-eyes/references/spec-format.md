# `.fresh-eyes/spec.md` — the truth about how the app should look and behave

Without written intent a vision model cannot tell a bug from a decision ("replies expand inline", "shared
price notes appear once above the list"). The spec is that intent, assembled from what the project already has
and confirmed by the user. It lives in git and grows with every run.

## Format

```markdown
# Spec — <app>
Updated: YYYY-MM-DD

## Global
- G-radius: cards and sheets use radius 16; drawer right corners are rounded. (source: packages/theme, 2026-09-23)
- G-icons: one entity, one icon — assistant → robot (tab bar and header). (source: design/, 2026-09)
- G-motion: UI transitions ≤ 300 ms, ease-out, interruptible; no animation on tab switch. (source: motion.ts)
- G-copy: one language per screen; no apologies; no internal mechanics in user text. (source: backlog)

## <screen-id> — <screen name>
Purpose: <one line>
- must <id>#1: <checkable rule> (source: <file/commit/decision>, <date>)
- must-not <id>#2: <checkable rule> (source: ...)
Reference: <design file / reference app screen>
```

Rules are **checkable sentences** ("replies open inline under the comment, without navigating to a Thread
screen"), not wishes ("nice replies"). Each rule has an id, a source and a date.

## Assembling it (`/fresh-eyes init`)

Read, newest first, and extract only statements about look and behaviour:
1. Design sources: design files/canvases, screenshots folders, reference images in the backlog.
2. Design tokens: theme colors, radii, spacing, motion constants and their comments.
3. Written decisions: decision logs, ADRs, plans, "Expected" fields of backlog entries, "user decision" notes.
4. Code comments that state a decision ("this card is gone by decision ..."), Maestro flow comments.
5. Commit messages of `fix(`/`feat(` commits (they often say "as in <reference app>").
6. External task boards / decision stores if the project uses one and it is reachable.

Write one rule per statement with its source. Keep the file short: global rules first, then the screens
from `config.json`, in that order.

## Conflicts

When two sources disagree, keep the newer one, mark the rule `⚠ conflict` (in a Russian spec `⚠ конфликт`)
with both versions and dates, and
ask the user at the end of the run (orchestrator only — subagents put the question into `questions[]`).
Never resolve a conflict silently, and never write "do not flag" into a conflict rule: a conflict is exactly
what the reviewer must surface (as an `unsure` finding plus a question) until the user decides.

## Growing it

- A finding the user confirms as a bug may become a rule (proposed, written only after the user agrees).
- A finding the user rejects ("intended") goes to `known.md` and is never flagged again.

## `.fresh-eyes/known.md`

```markdown
# Known and intended
- <screen-id>: <what looks odd but is intended> (confirmed by user, YYYY-MM-DD)
```
