---
name: push-without-asking
description: "Push main without asking, once work is committed and the test suite passes."
metadata:
  node_type: memory
  type: feedback
---

Push `main` whenever work is committed and the tests pass — do not stop to ask
for permission each time. The project owner chose this explicitly on 2026-11-13 after
three commits sat unpushed and a student install pulled stale code.

**Why:** `main` is what the installer tarball
(`https://github.com/CompassPoint-Mentorship/MuJoCo-Go2/archive/refs/heads/main.tar.gz`)
and every student laptop pulls from, so unpushed commits are invisible work
that actively misleads — the installer looks like it succeeded and delivers
old code.

**How to apply:** commit, run the suite, push — **then check CI on GitHub**
(`gh run list --workflow tests --limit 1`). A green local run is not enough:
files ignored by git exist on this disk and nowhere else, and CI failed nine
pushes in a row (2026-11-13/14) on a missing logo nobody looked for. Mention
the push in passing rather than asking first. Still stop and ask for anything genuinely
irreversible or outward-facing beyond this (force-push, rewriting history,
deleting branches, publishing elsewhere).

Related: [[github-issue-token]].
