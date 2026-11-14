# Gateway change: add `/labs/` for robo-dog.cpmentorship.org

**For whoever runs the nginx gateway (192.168.1.220).** One addition to the
existing `robo-dog.cpmentorship.org` server block. Nothing that exists today
changes. About ten minutes, and it can be undone by deleting the block.

---

## What and why

The site now has online labs (JupyterHub) running on the same upstream
machine, on a **separate port**. Requests under `/labs/` go there; everything
else keeps going exactly where it goes now.

| | Today | After |
|---|---|---|
| `/labs/…` | → `192.168.1.215:8500` (answers 404) | → **`192.168.1.215:8600`** |
| everything else | → `192.168.1.215:8500` | → `192.168.1.215:8500` (unchanged) |

It is a separate route on purpose: if the labs go down, the rest of the site
must keep working.

---

## 1. Add the WebSocket map (http level, once)

In the `http {}` context — e.g. a new file `/etc/nginx/conf.d/websocket-map.conf`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      '';
}
```

**If a `$connection_upgrade` map already exists in your config, skip this
step** — nginx refuses to start with two. `grep -rn connection_upgrade /etc/nginx/`
tells you.

## 2. Add the `/labs/` location (inside the existing server block)

In the `server { … server_name robo-dog.cpmentorship.org; … }` block that
listens on 443, **next to** the existing `location / { … }`:

```nginx
    # Saving a notebook with plots in it is several MB.
    client_max_body_size 50m;

    location /labs/ {
        proxy_pass http://192.168.1.215:8600;   # NO trailing slash
        proxy_http_version 1.1;
        proxy_set_header Upgrade            $http_upgrade;
        proxy_set_header Connection         $connection_upgrade;
        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering    off;
    }
```

If the server block already sets `client_max_body_size 1m`, change that line
to `50m` rather than adding a second one.

## 3. Test and reload

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Each line, and what breaks without it

| Line | Without it |
|---|---|
| `Upgrade` / `Connection $connection_upgrade` | notebooks open but **no cell ever runs** — Jupyter runs code over a WebSocket, with no fallback |
| `proxy_http_version 1.1` | the WebSocket upgrade is impossible |
| `proxy_read_timeout 3600s` | nginx cuts an idle notebook after 60 s: "kernel disconnected" |
| `proxy_buffering off` | the "starting your lab" page sits blank until the lab is up |
| no trailing slash on `proxy_pass` | JupyterHub serves itself *under* `/labs/` and must see the full path |
| `X-Forwarded-Proto $scheme` | the labs think visitors arrived over plain http |
| `client_max_body_size 50m` | saving a notebook with images fails with 413 |

## Please do not

- change `location / { … }` — the site stays as it is;
- cache anything under `/labs/` (no `proxy_cache`, no `expires`);
- add basic-auth or IP allowlists — sign-in happens on the site;
- expose ports **8400–8460** — those are old per-student labs whose link *is*
  the password.

---

## Checks

Before the change, `/labs/` answers 404. After it:

```bash
# 1. The hub answers through the gateway
curl -fsS https://robo-dog.cpmentorship.org/labs/hub/api
#    -> {"version": "6.2.0"}

# 2. The hub has no login page of its own: it sends people to the site
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
     https://robo-dog.cpmentorship.org/labs/hub/login
#    -> 302 https://robo-dog.cpmentorship.org/student/lab

# 3. The rest of the site is untouched
curl -fsS https://robo-dog.cpmentorship.org/healthz
#    -> ok
```

The WebSocket can only be proved by running code in a lab, which needs a
signed-in student, so that check is done from the class server. **When
checks 1–3 pass, say so**, and the site team runs it before switching the
labs on for students.

**Cloudflare:** WebSockets are on by default on every plan. If check 1 passes
but labs later show "kernel disconnected" immediately, look at Cloudflare →
Network → WebSockets.

**Port 8600** must be reachable from the gateway, as 8500 already is.

## Undo

Delete the `location /labs/` block (and the map, if you added it), then
`sudo nginx -t && sudo systemctl reload nginx`. Nothing else depends on it.
