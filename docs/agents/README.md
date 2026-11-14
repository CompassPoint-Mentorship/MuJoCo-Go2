# Agent instructions — which document goes to which agent

Several agents have worked on this platform: coding agents in this repository,
and an agent that runs the nginx gateway. Each gets a self-contained brief.

| Agent | Give it | Notes |
|---|---|---|
| A coding agent in this repo (Claude Code, etc.) | `docs/agents/CLAUDE.md` | loaded automatically through the root `CLAUDE.md`; other agents read `AGENTS.md` |
| The nginx gateway's operator — first setup | `docs/gateway/NGINX-REQUIREMENTS.md` | the whole contract: upstream, headers, checks |
| The nginx gateway's operator — adding online labs | `docs/gateway/GATEWAY-CHANGE-labs.md` | one change, with checks and undo |

## Memory

`memory/` holds notes a coding agent kept between sessions, copied here so
they travel with the repository rather than living only in one machine's
`~/.claude/projects/…/memory/`:

| Note | About |
|---|---|
| `memory/push-without-asking.md` | push `main` once tests pass, then check CI on GitHub |
| `memory/github-issue-token.md` | filing issues needs an organisation-owned token; reads lie because the repo is public |

When a lasting lesson is learned, add it to `CLAUDE.md` (rules) or here
(context), not only to one machine's memory.
