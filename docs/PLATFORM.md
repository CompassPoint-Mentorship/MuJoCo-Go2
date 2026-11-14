# The Robo Dog classroom platform — what has been built

A record of the platform as of **14 November 2026**: what exists, how the
pieces fit, why each was built the way it was, what has been verified live,
and what is still open. `DESIGN.md` covers the simulator itself; this covers
everything built around it to run a class. `OPERATIONS.md` is how to run it.

---

## 1. In one paragraph

A student programs a simulated Unitree Go2 in Python, in JupyterLab, with the
dog rendered beside the notebook. **The main way is their own laptop**: one
`curl … | bash` installs everything, and the lab keeps working with no
network. **The backup is an online lab** in the browser, for a laptop that
can't run it or was left at home. Both are reached from one website,
**robo-dog.cpmentorship.org**, where mentors run the class — add students,
publish course material, read handed-in homework, write feedback, email the
class — and students find their install line and their online lab.

## 2. The pieces

```
                                   Cloudflare (TLS, DNS)
                                          │
                       nginx gateway  192.168.1.220
                     ┌────────────────────┴────────────────────┐
                 everything else                            /labs/
                     │                                          │
        ┌────────────▼────────────┐            ┌────────────────▼───────────────┐
        │ classroom site  :8500   │            │ JupyterHub  :8600              │
        │ (go2-classroom)         │  pass ───▶ │ (go2-hub)                      │
        │ promo · /student ·      │            │  sign-in by pass only          │
        │ /console · installer ·  │            │  DockerSpawner → one lab per   │
        │ course pack · API       │            │  student, GPU, idle culler     │
        └──┬──────────┬───────────┘            └───────────────┬────────────────┘
           │          │ SMTP / IMAP                            │
           │          └──▶ Google mailbox (tech@cpmentorship.org)
           │                                                   ▼
           │ course pack, sign-in, hand-in          go2-hub-<name> lab containers
           ▼                                        (go2-lab image; student-work/<name>)
   student laptops: go2-lab (JupyterLab + simulator), offline-capable
```

All on one machine (`bumblebee`, 192.168.1.215), except the gateway,
Cloudflare and Google.

| Piece | Where | What |
|---|---|---|
| **`go2` package** | `go2/` | the simulator, the `Go2` API, the lab launcher, course delivery, the classroom strip |
| **Laptop installer** | `install/mac.sh` | macOS (Apple Silicon and Intel) and Linux; prints the release it installs; pinned to a commit |
| **Classroom site** | `classroom/` | FastAPI + SQLite + server-rendered pages, in a container |
| **Online labs** | `deploy/hub/` | JupyterHub 6.2 + DockerSpawner + idle culler, in a container |
| **Lab image** | `deploy/Dockerfile` | JupyterLab + simulator; runs standalone or under the hub |
| **Old per-port labs** | `deploy/go2-student.sh` | the first hosted model; being retired |
| **Gateway** | not in this repo | nginx; its requirements are in `docs/gateway/` |

## 3. What a student does

1. **Gets the install line** from `/student` (signed in with a join code or
   Google) and runs it. The installer names the exact release first, in
   California time, then installs from that commit's tarball — never a
   cached branch.
2. **Opens Go2 Lab.** Every launch pulls the class's current course material
   (a few hundred bytes to check; only changed files downloaded), then hands
   it into their notebook folder. Examples arrive **read-only**; the notebook
   tells them to Duplicate and work in the copy.
3. **Sees the classroom strip** at the top of the simulator pane:
   *course up to date / New from your class: homework_1.md [get it]*,
   *sign in*, *hand in*, *💬 New feedback*.
4. **Signs the laptop in once** (*sign in* → browser → "Connect this laptop as
   Ada? [Yes]"), then **hands in** a copy of any of their own files.
5. **Or opens the online lab** from `/student` — the same lab in the browser,
   started on demand, signed in already.

## 4. What a mentor does

All from `/console`, signed in with Google:

- **Roster** — add a student (name, optional email) → a join code; their
  online-lab state from JupyterHub; *Stop* a lab; *their work →* shows files,
  hand-ins, replies and connected laptops.
- **Mentors** — add a co-mentor by Google address.
- **Course material** — upload/remove files in the class pack, a mentor lab to
  write and test new exercises, publish from it, preview and hand out.
- **Feedback** — comment on a hand-in; the student sees it on `/student` and
  in their strip; optionally emailed.
- **Email** — invitations, class announcements (one email per student),
  feedback, all from tech@cpmentorship.org; an outbox; replies shown in the
  console and answerable there.

## 5. How the main things work

### Course material
The class works from **`courses/live/`** — seeded from the pack inside the
`go2` package the first time, and edited from the console. The bundled pack
stays as shipped, because it is what a laptop install contains.

The site publishes it at `/course/manifest.json` (sha256 per file) and
`/course/files/<name>`. Laptops and online labs **pull** it: only changed
files, verified by hash, written atomically, URLs carrying the hash so no CDN
can serve stale bytes. Offline is normal: six seconds at most, then the lab
starts with what it has. The pack is **public** by necessity — a laptop has no
session — so nothing goes in it that students may not read.

Handing over into a notebook folder (`go2/course.py` `sync`): examples are
written **read-only** and replaced in place each release, so the folder never
grows; a student's own edited copy is moved aside as `<name>.yours.ipynb`,
never destroyed.

### Signing in
| Who | How |
|---|---|
| Mentor | Google, and the address must be in `GO2_TEACHERS` or the console's mentor list; re-checked on every request |
| Student, on the site | Google (address on the roster) **or** a join code (no 0/O/1/I/L) |
| Student's laptop | device flow: one click in the browser; the laptop gets a key whose SHA-256 is all the server keeps; it can hand in and read the student's own feedback, nothing else |
| Student's online lab | the site writes a signed 60-second single-use pass; JupyterHub accepts only that; no URL carries a token |

### Email
Everything from **tech@cpmentorship.org** over SMTP with a Google app
password: invitations, announcements (one per student, checked against a
daily limit *before* any is sent), feedback, answers to replies. Only roster
and mentor-list addresses, each exactly one address. Replies are read over
IMAP **only if they answer an email the site sent** (matched by Message-ID),
read-only with `BODY.PEEK` so nothing in the mailbox changes.

### Online labs
JupyterHub under `/labs/`, a separate gateway route so the labs can never take
the website down. One container per student from the same lab image, GPU
attached, the same `student-work/<name>` folder; starts on demand (~6 s),
culled after an hour idle. The lab image allows `frame-ancestors 'self'` so the
split page can frame JupyterLab (JupyterHub's default is `'none'`).

## 6. Decisions, and the ones that were reversed

| Decision | Why |
|---|---|
| **Laptops first, web labs as backup** | a limited server; a lab that works offline; nothing to break the class if the server is down |
| Course pulled at launch + a strip, not pushed | laptops have no inbound connection; a running lab still hears about news |
| Read-only examples, replaced in place | a dated copy per release buried students in near-identical notebooks |
| Students' own files moved aside, never deleted | their work is never the thing we risk |
| JupyterHub for online labs | replaced per-port containers whose link *was* the password and worked only on the LAN |
| **Hub beside the site, not in front of it** | *reversed during the trial*: in front, a hub failure would take down the installer, `/student` and every laptop's course pull |
| One sign-in on the site, a pass into the hub | the hub has no login page; join codes work there too |
| Laptop sign-in by device flow, always one click | zero clicks would let a link connect someone else's laptop |
| Hand-in is a copy, never a sync | two-way sync asks a 14-year-old which version wins |
| **All email from tech@** | *reversed twice*: a shared SMTP sender, then drafts in each mentor's Gmail, then everything from tech@ for one recognisable sender |
| Gmail send API per mentor — **declined** | a server holding send-as-you tokens could email parents as their child's mentor |
| Otter-Grader — **declined** | 16 dependencies, including pandas and Playwright, on student laptops |
| Self-hosted Lora font | the privacy page promises no third-party requests |
| Versioned asset URLs | Cloudflare cached a stylesheet for four hours against new HTML |
| Server on UTC, people see California time | logs stay unambiguous; the installer and tmux show Pacific |
| Private-network addresses never reach a page | they were published once; the address remains in early git history |

## 7. Verified live

Each of these was run against the real deployment, not only in tests:

- Installer: release line, commit-pinned tarball, offline fallback (0.03 s refused, 6 s hung).
- Site: all five checks through Cloudflare and the gateway; `/healthz.json` reports `https`.
- Google sign-in redirect; join-code sign-in returning to where it started.
- Laptop pull from the live site: five files, then nothing, byte-identical mirror.
- Classroom strip with real processes: publish → flag within an interval → *get it* → file delivered read-only → flag clears.
- Laptop sign-in and hand-in with real processes; key on disk as `-rw-------`.
- Email: Google accepted the app password; a test email sent; IMAP read-only access confirmed.
- **Online lab through the public site**: join code → *Open my online lab* → lab in 6 s → split view → GPU frame (RTX 5090, EGL) → a notebook cell over `wss://` via Cloudflare walked the dog to x = 0.400; no token in any URL.
- The console's hub key: can list and stop labs; starting a lab, changing a user and reading tokens all refused (403).

166 tests; CI on every push (`.github/workflows/`).

## 8. Open items

**Needs a decision or a hand on the server**
- Retire the old per-port labs still running: `camp` (port 8401, *Retire old
  lab* in the console) and `testuser` (port 8400, not on the roster).
- A **dedicated sending mailbox** instead of tech@ — the app password also opens
  the whole mailbox over IMAP. A 4-character fragment of the current app
  password appeared once in a session's error output; rotating it is advised.
- Firewall ports **8500 and 8600** to the gateway (they are open to the LAN).
- The classroom container runs as a container, not a systemd unit; it restarts
  with Docker (`restart: unless-stopped`).
- The tailnet address is still in early git history (rewriting it means a force-push).

**Course**
- Units 02 and 03 are scripts, not notebooks; nothing after unit 03 (issues #14, #15).
- Answer keys need a mentor-only folder — the pack is public.
- A homework checker built into `go2` (proposed; Otter declined).

**Platform**
- Mentors cannot open a student's online lab to help live (needs a hub identity for mentors).
- Real robot (step 2) — `STEP2-REAL-ROBOT.md`; issues #1, #4–#11.
- Remove-a-student cleanup in `go2-student.sh` (#20); root LICENSE (#18).
