# Instructions for coding agents working on MuJoCo-Go2

Read this before changing anything. The root `CLAUDE.md` imports it, so
Claude Code loads it automatically; other agents should start here
(`AGENTS.md` points to it).

## What this repository is

A course for teenagers: program a simulated Unitree Go2 in Python. Three
products in one repo, on different clocks:

| Path | What | Read first |
|---|---|---|
| `go2/` | the simulator package students install | `docs/DESIGN.md` |
| `install/` | the laptop installer (`curl … \| bash`) | `README.md` |
| `classroom/` | the website: student pages, mentor console, email, API | `classroom/README.md` |
| `deploy/` | lab image, JupyterHub (`deploy/hub/`), old per-port labs | `deploy/README.md`, `deploy/hub/README.md` |
| `docs/` | all documentation, including this file | `docs/README.md` |

What has been built and why: `docs/PLATFORM.md`. How to run it:
`docs/OPERATIONS.md`.

## Commands

```bash
MUJOCO_GL=egl .venv/bin/python -m pytest -q        # the whole suite (~30 s)
shellcheck install/*.sh deploy/*.sh                # CI blocks on this
docker restart go2-classroom                       # site code changed (it is mounted)
docker build -t go2-lab:latest -f deploy/Dockerfile .
cd deploy/hub && docker compose up -d --build      # hub config changed
```

Deployment details, ports and secrets: `docs/OPERATIONS.md`.

## How the project owner wants work done

- **Push without asking**, once committed and the suite passes — then **check CI
  on GitHub** (`gh run list --workflow tests --limit 1`). Local green is not
  enough: see `docs/agents/memory/push-without-asking.md`. Ask before anything
  irreversible: force-push, history rewrites, deleting branches.
- **Small bugs: just fix them.** No demonstrations of the bug first.
- **Ask only for real decisions** — things only the owner can choose
  (providers, policy, who receives what). Routine judgement calls are yours.
- **Laptops are the primary way a class runs; the online labs are the backup.**
  Nothing on the server may be able to break a laptop install.
- **The server's resources are limited.** Prefer things that cost nothing while
  idle.
- **Every email goes from tech@cpmentorship.org.**
- People see **California time**; the server stays on UTC.
- Commit messages explain *why*, at length when it matters. Do not put personal
  email addresses in commit messages (a privacy hook blocks the command).
- GitHub (`gh`): token in `.env` as `GITHUB_ISSUE_TOKEN`; it must be
  organisation-owned — see `docs/agents/memory/github-issue-token.md`.

## Rules that exist because they were broken once

1. **Tests write only under `tmp_path`.** Every writable path in a fixture —
   database, course pack, hand-in folder — must be under `tmp_path`. A fixture
   that used the default course pack deleted a notebook from the live class.
2. **Ask git, not the disk.** A file that is `.gitignore`d still exists here, so
   local tests pass while every clone and CI run lacks it. The site's logo was
   missing from git for nine commits. `test_nothing_the_site_or_the_lab_serves_is_ignored_by_git`
   guards the served folders; think about it for anything new.
3. **Check what the browser actually loads.** A trial checked that the split
   page *contained* a frame and never loaded the framed page — whose
   `Content-Security-Policy: frame-ancestors 'none'` blanked it.
4. **Never print secrets.** Don't `cat .env` or `source` it in a command whose
   output you show; read it with `classroom.settings.load_dotenv()`. Sourcing it
   once printed part of an app password in an error message. When adding a
   setting, add it to `.env.example` too.
5. **`.env` changes need `--force-recreate`**, not `docker restart`, or the
   container keeps its old environment.
6. **Don't `pkill -f` a pattern that appears in your own command line** — it
   kills your shell. Kill by port: `ss -lntpH 'sport = :8500'` → pid.
7. **Cloudflare caches by file extension.** Asset URLs carry `?v=<hash>`;
   anything that must never be stale (installer, course pack, API) sends
   `Cache-Control: no-store`.
8. **Live checks use throwaway names** (`hubtest`, `frametest`) and are cleaned
   up afterwards: the container, `student-work/<name>`, and the roster row.
9. **No private-network addresses in pages or public docs.** A tailnet address
   was published once and is still in early git history.

## Never

- Set `GO2_ALLOW_REAL=1` on a shared server — it drives a 15 kg robot.
- Expose the old lab ports **8400–8460** — their link *is* the password.
- Put anything in the course pack that students may not read — it is public.
- Delete a student's work. Move it aside (`<name>.yours.ipynb`); never remove it.
- Mail an address that is not on the roster or the mentor list.
- Read anything in the mailbox except replies to mail the site sent.

## Other agents

`docs/agents/README.md` says which document goes to which agent — for example,
the nginx gateway's operator gets `docs/gateway/GATEWAY-CHANGE-labs.md`.
