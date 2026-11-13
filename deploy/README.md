# Hosted mode — one JupyterLab per student

The student installs nothing. They open a link and get an editor, a terminal
and the dog rendering inline in notebook cells.

```
student's browser  ──►  bumblebee  ──►  simulated dog
   (a link)              container        in the notebook
```

## Running a class

```bash
cd deploy
./go2-student.sh create alice     # allocate port, token, work dir
./go2-student.sh up     alice     # build (first time) and start
./go2-student.sh link   alice     # the link to send them
./go2-student.sh list             # everyone's port, status, link, GPU use
./go2-student.sh down   alice     # stop
./go2-student.sh sync   --all     # hand out fixed/new coursework
```

`create` copies the starter notebooks into the student's work directory. Ports
are allocated from **8400**, one per student, in creation order.

### Handing out updated coursework

`create` seeds once, so a notebook fixed after the class started never reaches
anyone. `sync <name>` or `sync --all` catches them up, and `-n` shows what it
would do without writing anything.

The rules live in the engine (`go2-course sync`), not in this script, so a
student on a laptop and a student on this server get the same material by the
same rules — two implementations of "replace what nobody touched" would drift
apart within a term. `GO2_COURSE_PACK=/srv/courses/go2-intro-2026.12` points a
whole class at a different pack without touching the image.

It keeps a copy of what it last handed over in `student-work/<name>/.go2-seed`
(hidden from JupyterLab's file browser). That copy is what makes updating safe:
comparing it with the student's file says whether they have *touched* it.

| Their file | What happens |
|---|---|
| missing | copied in |
| identical to the seed — never edited | replaced |
| edited, or from before the seed existed | **left alone**; the new version lands beside it as `<name>.updated-<date>-<time>.ipynb` |

Every update gets a name nothing is using, which is what makes this safe to
run repeatedly. A single fixed `.updated` name failed silently twice over: a
student who had run a cell in that copy owned it, so the next fix had nowhere
to go and was dropped, and replacing a file in place is invisible anyway —
**JupyterLab does not reload a notebook that is already open**, so a student
with the tab open sees yesterday's lesson with no sign that anything
happened. A dated name always lands, sorts newest-last beside their own file,
and appears in the file browser, which is the only signal they will notice.
Copies they never opened are removed as newer ones supersede them, so the
folder holds one.

Nothing restarts — the work dir is a bind mount — but a tab that was already
open still shows what it loaded. Tell students to **open the new file**
rather than reload: their old tab is a different file now.

The link is `http://<host>:<port>/lab?token=<token>` — the token *is* the
login, so treat the link as a password and send it to that student only.

## Where work lives

`student-work/<name>/` on the host, bind-mounted to `/home/student/work` in
the container. It survives rebuilds, restarts and image changes, so you can
update the image mid-course without losing anybody's code.

## One link, both panes

The student link points at `/proxy/8399/split`. That page frames JupyterLab on
the left and the live dog on the right, so **opening the link is all they do** --
nothing to arrange, no cell to run first.

Both frames are same-origin: the split page is itself served through
jupyter-server-proxy, so the Jupyter server is the origin for both and no CSP
relaxation is needed. Drag the grey divider to resize.

When the lab stops, the page says so and covers both frames. Without that,
JupyterLab puts up its own modal offering to reconnect to a server that has
been deliberately shut down — a dialog that blocks the page while telling the
student something untrue. The page polls Jupyter's `/api/status`; only a
network-level failure counts, so a simulator the supervisor is restarting does
not trigger it.

`./go2-student.sh up <name>` also prints a plain `/lab` link for anyone who
wants Lab on its own.

## The dog lives in a service, not in the kernel

`deploy/entrypoint.sh` starts `python -m go2.service` before JupyterLab, and
the container sets `GO2_SIM_URL`, so `Go2(sim=True)` in a notebook connects to
that service instead of building its own simulator. This is what makes the
split layout work -- there is a live dog to show before anyone runs a cell.

Consequences, all deliberate:

- **Restarting the kernel does not destroy the dog.** It keeps its position
  and whatever command it was last given.
- **Several notebooks drive the same dog.** That is what lets the panel show
  what your code is doing.
- **`dog.reset()` matters.** Because state persists, experiments should start
  from a known position. There is a reset button in the viewer too.
- The dog keeps walking if a cell raises. `dog.stop()`, or the stop button.

A local `pip install` sets no `GO2_SIM_URL`, so the simulator runs inside the
process as before and none of this applies.

The entrypoint supervises the service and restarts it if it ever exits. A
dead panel plus a notebook full of `ConnectionRefusedError` is a terrible
experience for a student who did nothing wrong; the dog coming back at the
origin is recoverable. Restarts are logged as `simulator service exited
(restart #N)`, so check the container log if a dog mysteriously teleports
home.

### A note on MuJoCo and threads

`MjData` is plain memory with no internal locking. Stepping it on the sim
thread while another thread calls `mj_forward` (reset) or `mjv_updateScene`
(render) is a data race, and it **segfaults the process** rather than raising.
Everything that touches `MjData` therefore goes through one re-entrant lock,
and `reset()` is applied by the sim thread rather than the caller's.

This is not theoretical: it killed the service in testing, leaving the panel
showing a stale frame while every notebook raised `ConnectionRefusedError`.
`test_reset_and_render_under_concurrent_load` guards it.

## HTTPS (needed for voice recording)

Browsers only allow `getUserMedia` -- the microphone -- on a **secure
context**: https, or localhost. Over plain http the record button in the
viewer reports `recording needs a secure page` and nothing else breaks.

A self-signed certificate is enough. Once the student clicks through the
browser warning the origin is https, `isSecureContext` is true, and recording
works:

```bash
cd deploy
./make-cert.sh                  # covers every address this host answers on
./go2-student.sh up alice       # restart to pick it up
```

Jupyter terminates TLS itself, so one certificate covers the lab, the proxy,
the split page and the viewer. `go2-student.sh` prints `https://` links
automatically once `deploy/tls/cert.pem` exists, and the containers fall back
to HTTP if it does not.

The certificate carries the LAN address, the tailnet address and the hostname
as subject alternative names -- browsers ignore CN, and an IP must appear as
an `IP:` entry or `https://192.168.1.215` will not match. Pass extra names or
addresses as arguments if you need them.

**The cost:** each student sees a browser warning once per machine and has to
choose *Advanced -> Proceed*. `make-cert.sh` prints the SHA-256 fingerprint so
they can check it matches rather than clicking blind. Teaching people to click
through certificate warnings is a bad habit, so it is worth saying out loud
that this one is expected and why.

A real certificate needs a public domain and DNS, which is a lot of setup for
a machine serving one local network. If you later want that without the
warning, Cloudflare Tunnel or a Tailscale `*.ts.net` certificate both give a
genuinely trusted name.

## Bandwidth

The panel polls JPEG frames. Defaults are 640x360 at 12 fps, about **320 KB/s
per watching student**, and polling stops entirely while the browser tab is
hidden -- students leave tabs open all day.

That is a tenth of what 960x540 at 20 fps costs. On a LAN either is fine;
over any remote link the defaults matter. Raise them with
`--width/--height/--fps` on `go2.service` if you want a nicer picture.

## Resources

Measured on a Ryzen 9 9950X + RTX 5090:

| Per student | Cost |
|---|---|
| CPU | ~3.2% of one core (simulation) |
| RAM | ~60 MB marginal |
| VRAM | ~150 MB (rendering context) |

A class of 30 is roughly **1 core, 2 GB RAM and 4.5 GB VRAM**. The simulator
is not the constraint; it comfortably shares the box with Isaac Sim.

## Rendering

Frames render offscreen via **EGL on the host GPU** (~0.3 ms at 640×480). The
container asks the NVIDIA toolkit for `graphics` capability, not just
`compute` — without `graphics` there is no EGL and rendering fails.

To run with no GPU at all, set `MUJOCO_GL=osmesa` in the student's env file
and drop the `deploy.resources` block from `docker-compose.yml`. Software
rendering is slower but needs no driver.

## The real robot is off, deliberately

`GO2_ALLOW_REAL=0` is set in both the Dockerfile and the compose file, and
`Go2(sim=False)` raises `PermissionError` without it.

This is not a formality. A hosted notebook can be opened from anywhere, and
the real backend drives a 15 kg robot that may have nobody standing next to
it. **Never set `GO2_ALLOW_REAL=1` on a shared teaching server.** Hardware
work belongs on a machine where a human is present with the remote in hand,
during a booked slot.

There is a test asserting the interlock holds (`test_real_mode_is_disabled_by_default`).

## Security

JupyterLab gives each student a shell inside their own container as an
unprivileged user, with their own work directory. Containers are isolated from
each other, but note:

- The token is the only authentication. Anyone with the link has that
  student's environment. Rotate by editing `LAB_TOKEN` and restarting.
- Ports bind all interfaces. Reach them over the tailnet, or restrict with
  ufw, exactly as for the Isaac Sim viewer.
- Students can `pip install` inside their own container. That is intentional,
  and it cannot affect anyone else.

## Updating the course code mid-term

```bash
cd deploy
for f in students/*.env; do
  n=$(basename "$f" .env)
  docker compose --env-file "$f" -p "go2-$n" up -d --build
done
```

Student work is bind-mounted, so it is untouched. Only the installed `go2`
package changes.
