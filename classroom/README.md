# The classroom site — robo-dog.cpmentorship.org

Three pages and a mirror:

| Path | Who | What |
|---|---|---|
| `/` | anyone | what this is; the promotional page |
| `/student` | a student | their install line, their lab link, what to do first |
| `/console` | a mentor | the roster: add a student, start/stop their lab, hand out updated coursework |
| `/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh` | anyone | the installer, served from this domain |

```bash
cd classroom && docker compose up -d --build      # how it runs in production
docker compose logs -f
```

or, for development, straight off the checkout:

```bash
python -m classroom                 # 127.0.0.1:8500
python -m classroom --host 0.0.0.0  # reachable from a proxy on another box
```

## Why the installer is mirrored here

So a student types the address on the board. The GitHub path has an
organisation name, a repository name, a branch and a directory in it, and a
fourteen-year-old copying that off a slide gets one of them wrong. The mirror
keeps the same path shape — so the two URLs are interchangeable — and serves
**this checkout's** copy with `Cache-Control: no-store`.

That last part is deliberate: `raw.githubusercontent.com` caches for about
five minutes, which is long enough for an install run right after a fix to
quietly not contain it. Mirroring and then caching would reproduce the bug we
mirrored to avoid.

A test asserts the bytes served are identical to `install/mac.sh` in the
repository, so the mirror cannot silently become a fork.

## Setting it up

### 1. Google sign-in

Google Cloud console → **APIs & Services** → **Credentials** → **Create
credentials** → **OAuth client ID** → **Web application**.

- Authorised JavaScript origin: `https://robo-dog.cpmentorship.org`
- Authorised redirect URI: `https://robo-dog.cpmentorship.org/auth/google/callback`

The redirect URI must match `GO2_BASE_URL` exactly — scheme, host, no
trailing slash. A mismatch there is the single most common reason sign-in
fails, which is why the app builds it from one setting rather than from the
incoming request.

On the consent screen, **External** with the app in *Testing* is enough for a
class; add each mentor as a test user. Students do not need Google at all —
they have join codes.

### 2. Settings

The app reads the environment, and falls back to `.env` in the repository root
when running by hand:

```bash
GO2_BASE_URL=https://robo-dog.cpmentorship.org
GO2_TEACHERS=tech@cpmentorship.org,someone-else@cpmentorship.org
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GO2_SESSION_SECRET=$(openssl rand -base64 32)     # or everyone is logged out on restart
GO2_CLASSROOM_DB=/srv/MuJoCo-Go2/classroom/classroom.db
GO2_PROVISION=1                                   # 0 = roster only, never touch docker
```

`GO2_TEACHERS` is the root of the mentor list: an allowlist on this machine,
not a role anyone can grant themselves through a signup form. Mentors added
from the console are kept in the database *in addition* to it, so a lost
database or a bad row can never lock the owners out — and entries from the
environment cannot be removed through the web page.

A mentor can add another mentor, by Google address, from the console. That is
a real grant: the console starts and stops containers on this machine. The
table records who added whom, removal takes effect on the next request rather
than when a session expires, and the environment list stays the backstop.

### 3. nginx, on your gateway

```nginx
server {
    listen 443 ssl http2;
    server_name robo-dog.cpmentorship.org;

    ssl_certificate     /etc/letsencrypt/live/robo-dog.cpmentorship.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/robo-dog.cpmentorship.org/privkey.pem;

    location / {
        proxy_pass http://10.0.0.x:8500;          # the box running this app
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name robo-dog.cpmentorship.org;
    return 301 https://$host$request_uri;
}
```

`X-Forwarded-Proto` matters: it is how the app knows the request arrived over
https, which is what makes the session cookie `Secure`. `python -m classroom`
starts uvicorn with `--proxy-headers`, so bind it to an address only your
gateway can reach.

### 4. Running it: docker

```bash
cd classroom
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` is the point: it comes back after a reboot without
anyone needing root on the machine, which a systemd unit would.

**The container is not a security boundary.** It holds the host's docker
socket, because starting a student's lab *is* a docker command — and a
process with that socket is root on the host. Running the app directly as a
user in the `docker` group has exactly the same power. What the container
buys is a restart policy, a pinned Python environment and the same
operational shape as the student labs; it does not buy safety from itself.
Keep port 8500 reachable only from the gateway.

Three details in `docker-compose.yml` are load bearing:

| | Why |
|---|---|
| repo mounted at the **same absolute path** (`/srv/MuJoCo-Go2`) | `go2-student.sh` writes host paths into `deploy/students/*.env`, and the **host** daemon resolves them later. Two different views of the path and a student's work directory mounts from nowhere. |
| `network_mode: host` | The lab links come from reading this machine's addresses. On a bridge network they would be `172.x`, which means nothing to a student. |

No application code is copied into the image: the repository is mounted, so
what the site serves — including `install/mac.sh` — is always this checkout,
not what an image was built from three weeks ago.

**systemd instead**, if you prefer it and have root:
`classroom.service.example` is ready to copy to
`/etc/systemd/system/classroom.service`. It runs as the repository's owner for
the same docker-group reason.

## Running a class

1. Sign in at `/console` with Google. To add a co-mentor, put their Google
   address in **Mentors** and send them the console link — nothing is emailed
   from here.
2. Type the student's name. `Ada Lovelace` becomes `ada-lovelace`: their
   container, their work folder, their port.
3. Leave **start a lab for them** ticked and the console runs
   `go2-student.sh create` and `up` in the background — minutes, the first
   time, while the image builds. The page polls and updates itself.
4. Give them the join code, or add their Google address and they can use that
   instead. Either signs them in at `/student`, which shows the install line
   and their lab link.
5. **Sync** hands out updated coursework to that student, by the rules in
   `go2/course.py` (the example notebook is read-only, so it is replaced in
   place).

**Forget** removes a student from this roster and nothing else. Their
container keeps running, their work folder stays where it is and their port
stays allocated — deleting someone's work is a decision for a person at a
terminal, which is the same rule `deploy/go2-student.sh` follows.

## What is deliberately not here yet

- **Student labs are not proxied through this domain.** The links on the
  student page are the local addresses the lab actually listens on,
  with their self-signed certificate and its browser warning. Putting them
  behind the public name needs a path prefix in JupyterLab
  (`--ServerApp.base_url`) and WebSocket forwarding, and it changes how the
  token is handled — worth doing, but not worth doing badly.
- **No email.** Join codes are read out or pasted into a chat.
- **No password reset, no account creation.** There are no passwords: Google
  or a join code, both issued by a mentor.

## Shape of the code

```
classroom/
  settings.py   every setting, from the environment, with no surprising default
  db.py         the roster: one SQLite table, no ORM
  labs.py       shells out to deploy/go2-student.sh -- the same rules a mentor types
  auth.py       Google for mentors, join codes for students, sessions for both
  app.py        the routes
  templates/    server-rendered HTML; no build step, no framework
  static/       one stylesheet
```

It is separate from the `go2` package on purpose. The simulator is what
students install on their own laptops, and it must keep working with no
network and no services; this is a service that owns a database and talks to
docker. Merging them would put a web framework in the dependency list of every
student install.

## Tests

```bash
pip install -e ".[dev,classroom]"
pytest tests/test_classroom.py -q
```

The two that matter most: a name typed into the console never reaches a
command line unless it matches `^[a-z][a-z0-9-]{1,31}$`, and a student's page
shows a lab token looked up from their own session row and from nothing the
request can influence. A lab link *is* a password.
