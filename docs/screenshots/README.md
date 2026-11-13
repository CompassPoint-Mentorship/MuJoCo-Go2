# Screenshots

Images used by the documentation, and kept deliberately: `docs/` is written,
not generated. They are the exception to the blanket `*.png` / `*.jpg` rule in
`.gitignore`, which exists to keep student scratch output — `dog_view.png` and
friends — from being committed by accident.

**Put documentation images here**, not in the repository root. A screenshot
pasted into the root is ignored on purpose, so it will look committed and
never be.

A note on names: files captured on macOS arrive as `Screenshot 2026-11-12 at
10.43.26 PM.png`, where the space before `PM` is U+202F, a narrow no-break
space. That is invisible in a terminal and needs escaping in a Markdown link
(`%E2%80%AF`), so give anything you reference from a document a plain ASCII
name when you add it.
