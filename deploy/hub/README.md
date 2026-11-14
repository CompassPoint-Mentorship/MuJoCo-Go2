# Online labs — JupyterHub

The backup way to take the course: a lab in the browser, for a student whose
laptop can't run the simulator or who forgot it today. Laptops are the main
way; nothing here is needed for them to work.

```bash
cd deploy/hub
docker compose up -d --build          # the hub, on port 8600
docker logs -f go2-hub
```

## What a student sees

On `/student` (once `GO2_HUB_PUBLIC=1`): **Open my online lab**. One click:

```
/student/lab                      the site writes a pass: who, 60 s, single use, signed
  → /labs/hub/login?pass=…        the hub checks it and signs them in — no login page
  → /labs/hub/spawn/<name>        starts their lab if it isn't running
  → /labs/user/<name>/proxy/8399/split    JupyterLab and the dog, side by side
```

No URL ever carries a token. A lab link on its own gets nobody in: without the
hub's cookie it sends the visitor to sign in on the site, which sends them
back with a fresh pass.

## How it is put together

| Piece | |
|---|---|
| `jupyterhub_config.py` | the hub: `/labs/`, DockerSpawner, culler |
| `go2_handoff.py` | sign-in that accepts only a pass from the site |
| `handoff_token.py` | the pass format; `classroom/handoff.py` writes the same one |
| `Dockerfile` | the official `jupyterhub:6.2.0` image + DockerSpawner + culler |

- **One container per student**, from the same `go2-lab` image as before, with
  the GPU attached (EGL rendering) and the same `student-work/<name>` folder.
  The image carries `jupyterhub==6.2.0` to match the hub, and its entrypoint
  starts `jupyterhub-singleuser` when the hub launches it.
- **Course material arrives like on a laptop.** The lab pulls the class pack
  on the way up, *before* starting the simulator (whose classroom strip checks
  immediately), and the strip offers anything newer later. No laptop sign-in
  in the strip: the hub already knows who this is.
- **Only while used.** `jupyterhub-idle-culler` stops a lab untouched for an
  hour; the next visit starts it again (`implicit_spawn_seconds`, since
  JupyterHub 4 shows a Launch button otherwise). Files stay either way.

Measured: the hub is **~160 MB, 0.2% CPU**; a running lab **~440 MB, ~7% CPU**,
the same as the per-port labs; a lab starts in **about 7 seconds**.

## What the trial proved, and what it changed

Run on port 8600 with a throwaway user, nothing live touched:

| Check | Result |
|---|---|
| pass → signed in, no login page | ✓ |
| same pass used twice | refused → `/student/lab?failed=1` (no loop) |
| lab link without the hub cookie | sent to sign in on the site |
| lab started by the hub | 7 s |
| split view under `/labs/user/<name>/` | ✓ |
| GPU inside the lab | RTX 5090, EGL, real frames |
| a notebook cell over a WebSocket | `dog at Pose(x=0.000, y=0.000, yaw=0.000)` |
| course pulled before the simulator | strip: "course up to date" |

Three things it caught, all fixed:

- Setting `base_url` *and* `bind_url` made JupyterHub silently rebuild the
  address on port 8000. Only `bind_url` is set now.
- JupyterHub 4+ no longer starts a stopped lab when its URL is visited, and
  DockerSpawner needs a named command because the lab image has an
  entrypoint and no `CMD`.
- **Putting the hub's proxy in front of the whole site was wrong.** If the
  hub fails, the site — install line, student pages, the course pull every
  laptop makes — would fail with it. The gateway routes `/labs/` here and
  leaves everything else going straight to the site.

## Switching it on

1. **Gateway:** add the `/labs/` location from
   `classroom/NGINX-REQUIREMENTS.md`, section 5 (WebSockets, long timeouts,
   no buffering).
2. **Check** from outside: `https://robo-dog.cpmentorship.org/labs/hub/api`
   answers with the version.
3. **Prove a cell runs** through the public address (the WebSocket check),
   then set `GO2_HUB_PUBLIC=1` in `.env` and recreate the classroom container.
   Students see **Open my online lab** from then on.
4. Retire the per-port labs (`go2-student.sh up`) once nobody uses them.

`GO2_HUB_SECRET` in `.env` is shared by the site and the hub; change it and
both must be restarted, or every sign-in fails (the site says so rather than
looping).

## The usual warning

Like the classroom container, the hub holds the Docker socket — it has to, to
start labs — which is root on the host in all but name. Expose only `/labs/`
through the gateway, and nothing else on port 8600.
