# MuJoCo-Go2

One Python API for a Unitree Go2 — simulated or real.

```python
from go2 import Go2

dog = Go2(sim=True)        # simulator, on this laptop
# dog = Go2(sim=False)     # the real robot over WiFi   (not built yet)

dog.stand()
dog.move(vx=0.4)           # walk forward at 0.4 m/s
dog.wait(3)
dog.stop()

print(dog.pose())          # Pose(x=1.2, y=0.0, yaw=0.0)
image = dog.camera()       # (480, 640, 3) uint8
```

Students write their code once. Moving to the real dog changes one argument.

## Three ways to use it

**Hosted (recommended for a class)** — the student installs nothing. They open
a link and get an editor, a terminal and the dog, side by side in one tab, all
running on a shared server. See [deploy/README.md](deploy/README.md).

```bash
cd deploy
./go2-student.sh create alice
./go2-student.sh up     alice     # prints the link to send them
```

**On their own laptop** — one command, no git, no Python, no admin password,
and it works offline afterwards. See [Installing on a laptop](#installing-on-a-laptop).

```bash
curl -fsSL https://raw.githubusercontent.com/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh | bash
```

**From source** — for mentors and anyone changing the simulator itself.

```bash
git clone https://github.com/CompassPoint-Mentorship/MuJoCo-Go2.git
cd MuJoCo-Go2
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[lab,dev]"
go2-lab                            # the same lab the installer sets up
```

## Installing on a laptop

Works natively on **macOS** (Apple Silicon or Intel) and **Ubuntu**. CPU only —
no GPU, no Docker, no server, and after the install no network either.

The student runs one line in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh | bash
```

or, if they would rather not touch a terminal, downloads
`Go2-Simulator-macOS.zip` from the releases page and double-clicks
**Install Go2 Simulator**. Build that zip with `install/make-zip.sh`; it holds
the installer and a note about Gatekeeper, not the simulator, so it never goes
stale.

Then they open **Go2 Simulator** from their Applications folder. A browser tab
opens with the notebook on the left and the dog on the right — the same page
the server serves.

| | Where |
|---|---|
| The program | `~/.go2` (its own Python; nothing system-wide) |
| The launcher | `~/Applications/Go2 Simulator.app` |
| Their work | `~/Documents/Go2 Simulator` |
| Uninstall | `~/.go2/bin/go2-uninstall` — leaves the notebooks alone |

Re-running the installer upgrades in place.

### Why the local viewer is a browser page, not a native window

MuJoCo has a perfectly good 3D window, and the obvious thing to ship for macOS
would be that. The installer sets up the browser lab instead, for three
reasons:

- **One UI.** A student who works in class and at home learns one layout, not
  two. The local page *is* the hosted page, served from `127.0.0.1`.
- **The microphone works for free.** Browsers grant `getUserMedia` on a secure
  context, and `localhost` counts as one. The self-signed-certificate dance the
  shared server needs simply does not arise here.
- **No `mjpython`.** macOS insists GUI windows own the main thread, which is
  why MuJoCo ships a separate `mjpython` launcher. A page has no such rule, so
  one code path covers macOS and Ubuntu.

The native window is still one command away, and `go2-run` re-execs itself
under `mjpython` on macOS so you never have to remember to:

```bash
go2-run my_script.py        # Go2(sim=True, viewer=True) opens a real window
```

### Commands the install adds

| Command | Does |
|---|---|
| `go2-lab` | Simulator + JupyterLab + viewer, one browser tab |
| `go2-lab --check` | Self-test: prints what works and what does not |
| `go2-run script.py` | Run a script; handles macOS's `mjpython` rule |

`go2-lab` picks free ports, mints a fresh token per launch, keeps the simulator
alive if it crashes, and takes everything down on Ctrl-C.

### Known constraints

- **Intel Macs** get MuJoCo 3.x: 4.x publishes macOS wheels for Apple Silicon
  only. The installer detects this and pins accordingly. Everything works; it
  is slower.
- **macOS 11+** — that is MuJoCo's own floor.
- The install pulls about 250 MB the first time, Python included.

## Watch it in a browser without the lab

A bare viewer, no JupyterLab, no notebooks:

```bash
python -m go2.web                   # then open http://<host>:8300/
```

Drive with `W`/`A`/`S`/`D`, space to stop, buttons for sit/stand, live pose
readout. Useful on a headless server, over SSH, or to show the thing to someone
without setting anything up.

```bash
python -m go2.web --camera head     # the robot's forward view instead
python -m go2.web --host 127.0.0.1  # local only
```

It is MJPEG over plain HTTP -- no WebRTC, no H.264, so **no NVENC session
limit**. One viewer costs a few percent of a core on top of the simulator.

> **No authentication.** It binds all interfaces by default, so anyone who can
> reach the port can drive the dog. It is a simulated dog, so the stakes are
> low, but use `--host 127.0.0.1` if that matters.

## The API

| Call | Does |
|---|---|
| `dog.say(audio)` | Play a sound from the dog (file, bytes or file object) |
| `dog.reset()` | Back to the start: origin, stopped, standing |
| `dog.stand()` | Stand still on all four legs |
| `dog.sit()` | Lower the body to the ground |
| `dog.move(vx, vy, wz)` | Walk at a body-frame velocity until told otherwise |
| `dog.forward(speed)` / `dog.turn(rate)` | Shorthands for `move` |
| `dog.stop()` | Stop moving, stay standing |
| `dog.pose()` | `Pose(x, y, yaw)` — metres, radians, world frame |
| `dog.velocity()` | The velocity currently commanded |
| `dog.camera(w, h)` | Forward-facing RGB image, `(h, w, 3)` uint8 |
| `dog.camera(..., camera="chase")` | Outside view that follows the dog. **Simulator only** |
| `dog.wait(s)` | Sleep while the dog keeps doing what it was told |
| `dog.close()` | Shut down (or use `with Go2(...) as dog:`) |

`vx` is forward, `vy` is left, `wz` turns counter-clockwise. Commands **persist**
until changed — the same as the real robot, which keeps walking until the next
command arrives. Velocities are clamped (`MAX_VX`, `MAX_VY`, `MAX_WZ`) so a typo
cannot ask the real dog for something alarming.

## The walking problem — read this before judging the sim

MuJoCo simulates the Go2's *body*: 12 leg motors, mass, contact. It has no idea
**how to walk**. Unitree's walking controller is closed firmware on the physical
robot and cannot be copied into any simulator. Load the bare model, command
nothing, and the dog folds onto the floor at 7.7 cm.

So the simulator has to supply walking itself. Right now it does the cheapest
honest version: `go2/gait.py` plays a **kinematic trot animation** while
`go2/backends/sim.py` integrates the commanded velocity into the robot's pose.

What that means, stated plainly:

- The dog **cannot fall over, trip or slip.**
- Commanded velocity is tracked **exactly** — there is no tracking error.
- Obstacles **do** block it: MuJoCo collision detection runs every tick, and
  motion into an obstacle is refused (turning still works, so you can rotate
  away from a wall rather than getting stuck).

That is enough for navigation, perception and behaviour work, which is what this
course is about. It is **not** a dynamics-accurate quadruped, and nobody should
present it as one.

### Upgrade path

Replacing the animation with a trained walking policy changes
`go2/gait.py` and `go2/backends/sim.py` only. **Student code does not change.**
The options, cheapest first:

1. *(current)* kinematic trot animation
2. a pretrained Go2 velocity-tracking policy (`policy.onnx`) dropped in
3. a policy trained in-house and exported to ONNX, committed here so every
   student gets it with `git pull`

On the real dog this problem does not exist — Unitree's controller is already
there and does the walking.

## Performance

Measured on a Ryzen 9 9950X:

| Dogs in one process | RSS | CPU (% of one core) |
|---|---|---|
| 1 | 370 MB | 3.2% |
| 8 | 963 MB | 24.9% |
| 16 | 1446 MB | 53.2% |

Scaling is linear: roughly **60 MB and 1/30th of a core per dog**. Offscreen
rendering costs ~0.3 ms/frame at 640×480. A laptop runs one instance without
noticing; a server could host a whole class.

## The arena

`go2/assets/arena.xml` — flat floor, three obstacles, a wall and a visual goal
marker. Any geom named `obs_*` blocks the robot, so adding obstacles for an
assignment means adding geoms with that prefix. Point `Go2(scene=...)` at your
own MJCF to use a different arena.

## Layout

```
go2/
  api.py              the Go2 class students use
  gait.py             stand-in trot animation (see "The walking problem")
  backends/
    base.py           the interface every backend implements
    sim.py            MuJoCo backend, in this process
    remote.py         client of a go2.service running elsewhere
    real.py           physical Go2 Pro — not implemented yet (step 2)
  web.py              browser viewer, split page, and the service's HTTP API
  service.py          `python -m go2.service` -- a long-lived simulator
  desktop.py          `go2-lab` / `go2-run` -- the whole lab on one laptop
  audio.py            clips the dog can say, and which one is playing
  notebook.py         panel()/show()/live()/drive() -- renders inline in Jupyter
  assets/
    arena.xml         our scene
    go2.xml, assets/  Go2 model vendored from MuJoCo Menagerie (BSD-3, Unitree)
  coursework/         starter notebook and examples -- copied into each
                      student's folder, hosted or local, and the only copy
install/              laptop installer (mac.sh), the download zip, Gatekeeper note
deploy/               hosted mode: Dockerfile, compose, go2-student.sh
tests/                pytest suite, headless
```

The only local change to the vendored model is a forward-facing `head` camera
used by `camera()`; it is marked in `go2/assets/go2.xml`. Upstream copies are
kept as `LICENSE` and `UPSTREAM_README.md` for diffing on upgrade.

## Status

- [x] **Step 1** — `Go2` API, MuJoCo backend, stand-in walking, arena, examples, tests
- [ ] **Step 2** — real Go2 Pro backend over WebRTC
- [ ] **Step 3** — trained walking policy, so the sim looks right
- [ ] **Step 4** — assignments and student setup guide
- [x] **Step 5a** — browser viewer (`python -m go2.web`), so the sim can be
      watched and driven from any machine with no local install
- [x] **Step 5b** — hosted mode: one JupyterLab container per student, a link
      each, nothing installed on their machine (`deploy/`)
- [x] **Step 5c** — laptop install: one command on macOS or Ubuntu, same lab,
      works offline afterwards (`install/`)

### About the real robot (step 2)

Our dog is a **Go2 Pro**, which matters:

- The Pro does **not** support `unitree_sdk2` / CycloneDDS low-level control —
  that is an EDU feature. The supported route is **WebRTC**, the same channel
  the phone app uses, which gives high-level commands plus camera and LiDAR.
  That is all this API needs.
- The Pro has **no onboard Jetson**, so code is *not* uploaded to the dog. It
  runs on a laptop and drives the robot over WiFi.
- The robot accepts **one client at a time** — the phone app has to be
  disconnected first. With one dog and many students, robot time is a booked
  slot, not shared infrastructure.

See `go2/backends/real.py` for the full plan and gotchas.

**Real-robot mode is off unless `GO2_ALLOW_REAL=1`.** `Go2(sim=False)` raises
`PermissionError` without it, and the hosted containers hard-set it to `0`. A
hosted notebook can be opened from anywhere, and the real backend drives a
15 kg robot that may have nobody next to it. Hardware is opt-in, on a machine
where a human is present with the remote in hand.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Headless; on a machine with no display set `MUJOCO_GL=egl` (GPU) or
`MUJOCO_GL=osmesa` (software).

## Licence

BSD-3-Clause. The Go2 model in `go2/assets/` is from
[MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie),
© Unitree Robotics, BSD-3-Clause — see `go2/assets/LICENSE`.
