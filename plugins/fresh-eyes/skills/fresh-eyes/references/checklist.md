# Review checklist

How a picky QA engineer + product designer looks at one screen. Used by the reviewer on every evidence pack.

## How to look

1. Look at the pictures first: `som.png` (numbered elements), then `crops/*`, then `sheet_*.png` (motion), then
   read `candidates.json`, `hierarchy.txt`, the spec excerpt and `known.md`.
2. Every candidate from the scripts gets an explicit yes/no with a reason. Scripts over-report on purpose.
3. Then look beyond the candidates, section by section below. Most real bugs are not in `candidates.json`.
4. Point at elements by Set-of-Mark number and crop path, never by coordinates you estimated.
5. Small details (corner radius, 1–2 px misalignment, truncation "…", icon glyph) are judged on crops, not on
   the 1000 px overview. If no crop covers it, ask for one in `questions[]` / the verifier's requests.
6. A finding needs a frame. Never report a bug from code, a guess or a single hierarchy line.
7. "Could not see it" is not "it is fine": say what evidence is missing.

## Display defects (one frame shows it)

- **Clipped content**: a row, card or grid cut by its container (only a sliver of the last row visible),
  content under the tab bar / home indicator / status bar, a bottom sheet whose bottom is off screen.
- **Truncated text**: "…" or a hard cut where the full text matters (menu items, buttons, titles, chips).
  Compare the hierarchy's full text with what the crop shows.
- **Overlap / occlusion**: text over text, a card over a card, a floating button over the last list item,
  a toast under a sheet, a keyboard over the field or its suggestions.
- **Placeholder shown as data**: "null", "undefined", "0", "$ 0", "Pinned Location", lorem, a stock photo as
  an avatar, an e-mail where a handle belongs, untranslated keys.
- **Missing / broken media**: grey boxes, broken image icons, stretched or pixelated photos, wrong crop.
- **Spacing and alignment**: unequal gutters between siblings, misaligned baselines, a lone element off-grid.
- **Shape**: corner radii that differ from siblings or the design tokens (sharp where rounded is expected),
  one-sided rounding, hairline seams between cells.
- **Consistency**: the same entity with two icons (header vs tab bar), two components for one meaning
  (system alert next to a branded dialog), different styles for the same control on sibling screens.
- **Contrast / theme**: pale on pale, white on white, a dark map in a light app, colors that ignore the theme.
- **Language**: mixed languages on one screen, broken plurals ("1 comments"), wrong date words ("0 days ago").

## Behaviour defects (need an action, video or two frames)

- **Double fire**: rapid taps open two sheets / send twice / navigate twice. Oracle: dismiss once, nothing left.
- **Dead or wrong tap**: something looks tappable but does nothing; the label says one thing, the tap does another.
- **Overlay order**: a sheet slides under a card; tapping outside does not dismiss; a scrim does not block.
- **Interrupted animation**: Back / tap / drag during a transition leaves a half-open state, an invisible window
  that eats taps, or a jump.
- **Focus and keyboard**: focus without keyboard, keyboard covering the field, Back with keyboard closes the
  whole sheet and loses input, suggestions hidden under the keyboard, keyboard that cannot be dismissed.
- **Lost state**: background/foreground or kill/relaunch loses a draft, a reply, a selection, a scroll position.
- **Stale UI**: an indicator that stays after the data changed (a "✓" after editing), a restored item still grey.
- **Flow vs decision**: a flow that opens a new screen where the spec says inline (or the reverse).

## States

### default
The normal populated screen. Everything above applies.

### empty
No data yet. Expect a designed empty state with a next action, not a blank area or a lonely header.

### loading
Frames during a slow request. Expect a progress indicator or skeleton; no result title above an empty list.

### error
A failed request. Expect an honest message and a retry; not "nothing found", not a silent blank.

### offline
No network. Offline must not look like "empty" or "not found"; cached data is labelled as cached.

### long-text
Longest realistic strings and many items: last row fully reachable, no truncation of meaning, fields grow.

### dark
Dark theme (and system/app theme mismatch). Contrast, images, maps and system bars follow the theme.

### font-xl
Largest accessibility font size. Nothing overlaps or clips; buttons stay inside the screen.

### small-screen
Smallest supported phone. Titles do not run under buttons; CTAs stay on screen; sheets fit above system bars.

### keyboard
Keyboard open on every input: the field, its counter, suggestions and the send/next button stay visible.

## UX rubric (improvements → IMP)

Judge each screen against the project's reference apps (from config) and these questions:

a. **Interaction model**: is there a familiar pattern in the reference apps, and how does this differ?
b. **Ready to act**: focus and keyboard where the user will type, the first tap does the obvious thing.
c. **Honest states**: loading, empty, error, offline, deleted — each says what happened and what to do.
d. **Hierarchy**: the main thing is above the fold; no duplicated text (shared notes once above a list,
   not in every card); no prose where a label works.
e. **Words**: short, no apologies, no internal mechanics ("the live search returned nothing"), one language.
f. **One system**: same component for the same job, same icon for the same entity, tokens for radii/colors.
g. **Motion**: purposeful, short (within the spec's ceiling), interruptible, follows the finger, respects
   reduce-motion; no animation on very frequent actions (tab switches).
h. **Affordances**: what looks tappable is tappable; disabled looks disabled; the label equals the action.

An IMP must name the concrete change ("show the price note once above the list") and the reason.

## Severity

- **high**: blocks a task, loses data, shows wrong data as true, breaks navigation, or is visible on a primary
  screen to every user (clipped main content, double send, stuck invisible overlay).
- **medium**: clearly wrong but has a workaround or affects a secondary path (truncated menu item, wrong icon,
  sharp corners on a main surface, keyboard covering a secondary field).
- **low**: polish (1–2 px misalignment, slightly long transition, small tap target with enough spacing).

## Do not flag

- The OS status bar, OS keyboard internals, emulator chrome, developer overlays (LogBox, perf monitors).
- Anything listed in `known.md` (intentional behaviour confirmed by the user).
- Content that is clearly test data, unless the screen shows it wrongly.
- Partially visible items at the edge of a scrollable list when a swipe brings them fully into view.
