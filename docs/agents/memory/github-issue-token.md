---
name: github-issue-token
description: How to file GitHub issues for MuJoCo-Go2 — token location and the org-ownership requirement
metadata:
  type: project
---

Issues for `CompassPoint-Mentorship/MuJoCo-Go2` are filed with a fine-grained
PAT read from `GITHUB_ISSUE_TOKEN` in `/srv/MuJoCo-Go2/.env` (gitignored as of
2026-11-12). `gh` is installed at `~/.local/bin/gh`, not on the system PATH.

**Why:** the repo is owned by the *organization* CompassPoint-Mentorship, not
by the `cp-mentorship` user. A fine-grained PAT whose resource owner is the
user account can never write issues there, however its permissions read — and
because the repo is public, reads still return 200, so a token can look fine
and fail only on write.

**How to apply:** the token must have resource owner = the organization,
repository access including MuJoCo-Go2, and Issues: Read and write. Verify
with a POST, never a GET. `gh issue create` uses GraphQL and reports this less
clearly than the REST endpoint does.
