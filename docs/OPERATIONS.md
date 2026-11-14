# Operations — running robo-dog.cpmentorship.org

How to run, update, check and fix the platform. `PLATFORM.md` explains what it
is; this is what to type.

Everything runs on **bumblebee** (192.168.1.215) from the checkout at
`/srv/MuJoCo-Go2`, which is mounted into the containers — the site serves the
files on disk, so a `git pull` changes the site on its next restart.

---

## What runs

| Container | Started by | Port | What |
|---|---|---|---|
| `go2-classroom` | `classroom/docker-compose.yml` | 8500 (host network) | the website, console, installer mirror, course pack, API |
| `go2-hub` | `deploy/hub/docker-compose.yml` | 8600 | JupyterHub and its proxy, for `/labs/` |
| `go2-hub-<name>` | JupyterHub, on demand | internal | one student's online lab; removed when stopped |
| `go2-lab-<name>` | `deploy/go2-student.sh` | 8400+ | **old** per-port labs, being retired |

All are `restart: unless-stopped`, so they come back after a reboot.

| Image | Built from |
|---|---|
| `go2-classroom:latest` | `classroom/Dockerfile` (no code inside; the checkout is mounted) |
| `go2-hub:latest` | `deploy/hub/Dockerfile` (official `jupyterhub:6.2.0` + DockerSpawner + culler) |
| `go2-lab:latest` | `deploy/Dockerfile` (JupyterLab + simulator + `jupyterhub==6.2.0`) |

Outside this machine: **Cloudflare** (DNS, TLS at the edge), the **nginx
gateway** at 192.168.1.220 (requirements in `docs/gateway/`), **Google** (the
OAuth client and the tech@ mailbox).

## Configuration: `.env`

One file, mode 600, never committed. `.env.example` lists every key. Values
with spaces or `<…>` are quoted, so it also loads in bash
(`set -a; . ./.env; set +a`).

| Key | Used by | Notes |
|---|---|---|
| `GO2_BASE_URL` | site | `https://robo-dog.cpmentorship.org`; Google's redirect URI is built from it |
| `GO2_SESSION_SECRET` | site | changing it signs everyone out |
| `GO2_TEACHERS` | site | comma-separated mentor Google addresses; the root of the mentor list |
| `GO2_TRUSTED_PROXY` | site | the gateway's address; `X-Forwarded-*` believed only from it |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | site | Google Cloud project `cpmentorship-robo-dog` |
| `GO2_COURSE_PACK` | site, `go2-student.sh` | `/srv/MuJoCo-Go2/courses/live` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | site | Gmail SMTP as tech@, with a Google **app password** |
| `GO2_HUB_SECRET` | site **and** hub | signs the pass into the online lab; change both together |
| `GO2_HUB_PUBLIC` | site | `1` shows *Open my online lab* to students |
| `GO2_HUB_API_TOKEN` | site **and** hub | the console's key to JupyterHub: list and stop labs only |
| `GITHUB_ISSUE_TOKEN` | people | for `gh`; must be an **organisation-owned** fine-grained PAT |

**After changing `.env`, recreate — don't restart.** `docker restart` keeps
the old environment:

```bash
cd /srv/MuJoCo-Go2/classroom && docker compose up -d --force-recreate
cd /srv/MuJoCo-Go2/deploy/hub && docker compose up -d --force-recreate
```

## Updating

```bash
cd /srv/MuJoCo-Go2 && git pull
docker restart go2-classroom                               # site code (mounted)
docker build -t go2-lab:latest -f deploy/Dockerfile .      # lab image, if go2/ or deploy/ changed
cd deploy/hub && docker compose up -d --build              # hub, if deploy/hub/ changed
```

A rebuilt lab image reaches a student the next time their online lab starts.
To apply it now, **Stop** their lab in the console; their files stay.

Laptops update themselves: course material at every launch, and the strip
says when a newer simulator is available (they re-run the install line).

## Health

```bash
curl -fsS https://robo-dog.cpmentorship.org/healthz          # ok
curl -fsS https://robo-dog.cpmentorship.org/healthz.json     # "scheme":"https" means the gateway forwards correctly
curl -fsS https://robo-dog.cpmentorship.org/labs/hub/api     # {"version": "6.2.0"}
docker ps --format '{{.Names}}\t{{.Status}}' | grep go2
docker logs --tail 50 go2-classroom          # or go2-hub, go2-hub-<name>
```

Resource use, measured: site ~50 MB; hub ~160 MB; each running lab ~440 MB
and ~7% of a core, ~150 MB of GPU memory.

## Everyday tasks

| Task | Where |
|---|---|
| Add a student | console → *Add a student*; send the invitation from the row |
| Add a mentor | console → *Mentors*; or `GO2_TEACHERS` in `.env` (+ recreate) |
| Publish course material | console → *Course material* → upload, or publish from your lab |
| Read homework, write feedback | console → a student → *their work →* → a hand-in |
| Email the class | console → *Email the class* |
| See replies | console → *Replies* (checked every 3 min, or *check now*) |
| Stop a stuck online lab | console → the student's row → *Stop* |
| Retire an old per-port lab | console → *Retire old lab*; or `deploy/go2-student.sh down <name>` |

## Secrets: rotating

| Secret | How | Effect |
|---|---|---|
| `GO2_SESSION_SECRET` | new value, recreate site | everyone signs in again |
| `GO2_HUB_SECRET` | new value, recreate site **and** hub | none, if both change together; mismatched, sign-in shows an error |
| `GO2_HUB_API_TOKEN` | new value, recreate site **and** hub | none |
| App password | myaccount.google.com/apppasswords as tech@ → new one, delete the old; set `SMTP_PASSWORD`; recreate site; *send a test to me* | none |
| Google client secret | Google Cloud → Clients → *Add secret*, then delete the old | none |
| A laptop's key | console → student → *Disconnect* | that laptop signs in again |

## When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| Online lab: JupyterLab half blank | the lab forbids framing | the lab image must carry `deploy/jupyter_server_config.py` (`frame-ancestors 'self'`); rebuild and *Stop* the lab |
| Online lab: "kernel disconnected", cells never run | WebSockets or timeouts at the gateway | `docs/gateway/GATEWAY-CHANGE-labs.md` |
| *Open my online lab* shows "did not accept the sign-in" | `GO2_HUB_SECRET` differs between site and hub | same value in `.env`, recreate both |
| Page looks unstyled / old after an update | a cached stylesheet | asset URLs are versioned; a hard reload (⌘⇧R) clears the browser's copy |
| Google sign-in: `redirect_uri_mismatch` | `GO2_BASE_URL` ≠ the redirect URI registered with Google | make them identical: `https://robo-dog.cpmentorship.org/auth/google/callback` |
| Console: "email is not set up" | `SMTP_PASSWORD` empty or wrong | new app password, recreate, *send a test to me* |
| A laptop strip says *offline* | the site is unreachable from it | the lab still works; it catches up next time |

## Packing and backup

```bash
tools/pack.sh                 # code + docs: dist/robo-dog-<date>-<commit>.tar.gz
tools/pack.sh --with-state    # also dist/robo-dog-state-<date>.tar.gz (secrets + student data)
```

The code pack is a `git archive` of the current commit: everything needed to
rebuild the platform, and no secrets. The state pack holds `.env`, the
classroom database, the live course pack, hand-ins, student work, the hub's
database and cookie secret, the old labs' env files and the TLS files — **it
contains passwords and children's work**: keep it encrypted and off shared
drives.

To restore on a new machine: unpack the code, unpack the state over it, build
the three images (above), start the site and the hub, and point the gateway at
the new address.
