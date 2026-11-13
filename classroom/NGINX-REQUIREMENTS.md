# Requirements: `robo-dog.cpmentorship.org` on the gateway

**For whoever configures nginx on the gateway.** This is the whole contract
between your gateway and the classroom app. The app is written and running;
nothing in it needs to change to meet this, and nothing here needs anything
from the app's author.

---

## 1. What is being published

One origin, `https://robo-dog.cpmentorship.org`, in front of one upstream
HTTP service. It is a small Python (uvicorn/ASGI) app that serves a public
landing page, a student page, a mentor console and a copy of a shell script.

| | |
|---|---|
| Upstream host | `bumblebee` — `192.168.1.215` (LAN) or `100.84.247.85` (tailnet) |
| Upstream port | `8500`, plain HTTP |
| Health check | `GET /healthz` → `200`, body `ok` |
| Expected traffic | tens of requests per class session; one mentor, up to ~30 students |

Use the tailnet address if the gateway is on the tailnet — it needs no
firewall holes. Otherwise the LAN address, and the upstream box will be
firewalled to accept `8500` only from the gateway.

**The app is up and answering on both addresses now**, so the gateway can be
configured and tested immediately:

```
$ curl -fsS http://192.168.1.215:8500/healthz     # -> ok
$ curl -fsS http://100.84.247.85:8500/healthz     # -> ok
```

It listens on `0.0.0.0:8500` while the gateway is being set up. Once you know
the address nginx will connect from, say so and port 8500 will be firewalled
to that host alone -- nothing else has any business reaching it.

---

## 2. Hard requirements

These are not style preferences; each one breaks something specific.

### 2.1 Pass the real scheme and host

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

`X-Forwarded-Proto` is how the app learns the request arrived over https.
Without it the session cookie is not marked `Secure`, and Google sign-in is
compared against the wrong scheme. The app runs uvicorn with `--proxy-headers`
and will trust these from the gateway.

### 2.2 Do not rewrite, strip or normalise paths

Everything under `/` goes to the upstream **unchanged**, including this one:

```
/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh
```

That path looks like a GitHub URL because it is deliberately identical to one
— students are told to run it, and the two addresses have to be
interchangeable. A `location` block that rewrites, redirects, lower-cases or
collapses it breaks the install line printed on every student's page.

Use `proxy_pass http://UPSTREAM;` with **no trailing slash and no URI part**,
so nginx forwards the original path.

### 2.3 Do not cache anything

No `proxy_cache`, no `expires`, and do not add `Cache-Control` of your own.

The app sends `Cache-Control: no-store` on the installer deliberately: the
five-minute CDN cache on `raw.githubusercontent.com` is the exact bug this
mirror exists to avoid, where a student installs a version that is missing the
fix they were told to get. A cache on the gateway reintroduces it.

### 2.4 TLS, and redirect http → https

A real certificate (Let's Encrypt is fine). Port 80 redirects to https:

```nginx
server {
    listen 80;
    server_name robo-dog.cpmentorship.org;
    return 301 https://$host$request_uri;
}
```

Students will be told to type the bare domain. HSTS is welcome
(`Strict-Transport-Security: max-age=31536000`) but not required.

### 2.5 OAuth redirect must reach the app untouched

`GET /auth/google/callback?...` carries the query string Google sends. It must
arrive complete; do not strip query parameters anywhere on this server.

---

## 3. Suggested configuration

```nginx
upstream robodog {
    server 100.84.247.85:8500;      # or 192.168.1.215:8500
    keepalive 16;
}

server {
    listen 443 ssl;
    http2 on;
    server_name robo-dog.cpmentorship.org;

    ssl_certificate     /etc/letsencrypt/live/robo-dog.cpmentorship.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/robo-dog.cpmentorship.org/privkey.pem;

    # A form post is a few hundred bytes; nothing here uploads.
    client_max_body_size 1m;

    access_log /var/log/nginx/robodog.access.log;
    error_log  /var/log/nginx/robodog.error.log;

    location / {
        proxy_pass http://robodog;          # no trailing slash: path passes through
        proxy_http_version 1.1;
        proxy_set_header Connection         "";
        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;

        proxy_connect_timeout 5s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
    }
}

server {
    listen 80;
    server_name robo-dog.cpmentorship.org;
    return 301 https://$host$request_uri;
}
```

DNS: an `A` record (and `AAAA` if the gateway has v6) for
`robo-dog.cpmentorship.org` pointing at the gateway.

---

## 4. How to check it worked

```bash
# 1. the landing page
curl -fsS https://robo-dog.cpmentorship.org/ | grep -o '<title>.*</title>'
#    -> <title>Robo Dog — program a robot dog</title>

# 2. health
curl -fsS https://robo-dog.cpmentorship.org/healthz        # -> ok

# 3. the installer mirror: path intact, right type, NOT cached
curl -sS -D - -o /dev/null https://robo-dog.cpmentorship.org/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh \
  | grep -iE 'HTTP/|content-type|cache-control'
#    -> HTTP/2 200
#    -> content-type: text/x-shellscript; charset=utf-8
#    -> cache-control: no-store, max-age=0

# 4. the script itself is a script, not an error page
curl -fsSL https://robo-dog.cpmentorship.org/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh | head -1
#    -> #!/bin/bash

# 5. https is being reported to the app: the session cookie must say Secure
curl -sS -D - -o /dev/null https://robo-dog.cpmentorship.org/student | grep -i set-cookie
#    -> set-cookie: session=...; path=/; Secure; HttpOnly; SameSite=lax
```

Check 5 is the one that catches a missing `X-Forwarded-Proto`. If `Secure` is
absent, the header is not arriving. (Use `-D -` rather than `-I` on the
pages: they answer `GET`, while `/healthz` and the installer also answer
`HEAD`.)

---

## 5. Out of scope, and please do not improvise it

**Student lab containers (ports 8400–8460 on the upstream box) must not be
exposed through this gateway yet.** They are JupyterLab instances whose URL
*token is the login*; publishing them needs a path prefix configured inside
each container (`--ServerApp.base_url`), WebSocket upgrade handling, and a
decision about how the token travels. That work is planned on the app side.
Proxying them now, without the base-path change, produces a lab that loads a
blank page and a mentor who cannot tell why.

Also out of scope: basic-auth or IP allowlists in front of anything. The app
does its own authentication — Google for mentors (an allowlist on the server),
join codes for students — and an extra password in front of `/student` would
defeat the point of a link a student can open on a school laptop.

---

## 6. Contact points

- Upstream service: `systemctl status classroom` on `bumblebee`,
  logs in `journalctl -u classroom`.
- App config, including the public name it builds OAuth redirects from:
  `GO2_BASE_URL=https://robo-dog.cpmentorship.org`. If the public name ever
  changes, it must change in both places on the same day or Google sign-in
  stops working.
- Full app documentation: `classroom/README.md` in the
  `CompassPoint-Mentorship/MuJoCo-Go2` repository.
