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
Needs **Python 3.11 or newer**; a student never hits this, because the
installer downloads its own 3.12. If your system `python3` is older — Ubuntu
22.04 still ships 3.10 — use `uv venv --python 3.12 .venv` instead of the
line below.

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
`Go2-Lab-macOS.zip` from the releases page and double-clicks
**Install Go2 Lab**. Build that zip with `install/make-zip.sh`; it holds
the installer and a note about Gatekeeper, not the simulator, so it never goes
stale.

The install opens by naming the release it is about to put on the machine,
before it downloads or changes anything:

```
==> Go2 simulator installer  (Darwin arm64, into /Users/alice/.go2)
==> Release: CompassPoint-Mentorship/MuJoCo-Go2 main @ d58c968   (2026-11-12 21:07 PST)
```

The time is the class's own — California, or whatever `GO2_TZ` names. GitHub
answers in UTC, and "05:07Z" for a push somebody made at 9 last night is a
puzzle rather than an answer, which is the opposite of what this line is for.

That line is also a pin. Asking GitHub which commit `main` is at, then
downloading *that commit's* tarball, is what makes the printed version the
installed version: a branch tarball is served from a CDN that caches for
about five minutes, so an install run right after a fix can otherwise pull
the code without it and look perfectly successful. The commit is recorded in
the build stamp too, so `go2-lab --version` can still answer "which code is
this?" weeks later. When GitHub cannot be reached the install carries on from
the branch tarball and says that it could not check.

Then they open **Go2 Lab** from their Applications folder. A browser tab
opens with the notebook on the left and the dog on the right — the same page
the server serves.

| | Where |
|---|---|
| The program | `~/.go2` (its own Python; nothing system-wide) |
| The launcher | `~/Applications/Go2 Lab.app` |
| Their work | `~/Documents/Go2 Notebooks` (installs predating the rename keep `Go2 Simulator`) |
| Uninstall | `~/.go2/bin/go2-uninstall` — leaves the notebooks alone |

When it finishes, the installer offers to start the lab (`Return` for yes),
so the first run needs nothing found or clicked. `GO2_NO_LAUNCH=1` turns that
off and `GO2_START_NOW=y` answers in advance; with no terminal to ask — a
scripted or class-wide install — it never opens a window.

Re-running the installer upgrades in place. It refuses to do that while the
lab is running — replacing files underneath a live process shows up later as
an ImportError in a notebook, and the running copy would keep the old code in
memory anyway, so the fix would look like it had not worked. Close the Go2 Lab
window first; from a terminal, `GO2_STOP_RUNNING=y` stops it for you.

### Course packs

The engine and the course are different products on different clocks: the
simulator changes monthly, the notebooks change on the morning somebody finds
a typo. So the engine knows nothing about any particular course — it knows how
to find a **pack** and hand it out.

A pack is a directory:

```
course_pack/
  course.toml        id, title, version, and the units in order
  units/             the files a student gets
```

`go2-lab` and `go2-student.sh sync` resolve one the same way:

```
--course <path>   >   $GO2_COURSE   >   ~/.go2/courses/<id>   >   the bundled pack
```

The bundled pack ships inside the wheel so a laptop install has something to
open with no network and no second step. It is the default, not a special
case: point `--course` at another directory and it is replaced entirely.

```console
$ go2-course show
Programming a Robot Dog 2026.11.12
  1. Hello, dog  -- Connect to the dog, move it, read its pose.
  2. Walk a square  -- Sequence moves and turns; see why a command persists.

$ go2-course sync --into ~/go2-lab        # what the lab does at every launch
```

Course material is **not** a pip package, deliberately. A student should never
need a second install step, and a syllabus fix should not need a release.

### Updating the coursework on a laptop

**Laptops are how a class runs; the hosted labs are the backup.** So every
`go2-lab` launch first **pulls the class's current material** from the class
site (`https://robo-dog.cpmentorship.org/course`) into
`~/.go2/courses/<id>/`, then hands it over:

```
[go2] course material from the class: 1 new or changed
```

Only files whose hash changed are downloaded, a download that does not match
its hash never replaces a good file, and with no network the laptop says so
and starts with what it already has — a lab that cannot open on a bus is a
lab that fails at the worst moment. `GO2_COURSE_URL=off` turns pulling off;
naming a pack with `--course` or `$GO2_COURSE` skips it too, so a mentor
testing a draft gets the draft.

**A lab left open all week still hears about new material.** The top of the
simulator pane — always on screen beside the notebook — is a **classroom
strip**. The simulator service checks the class site in the background every
ten minutes, and the strip says where things stand:

```
Programming a Robot Dog      course up to date · checked 2 min ago  [ check now ]
Programming a Robot Dog      New from your class: homework_1.md     [ get it ]
```

The strip is built for more than this. The service sends a title and a list
of items — text, a tone, perhaps a button — and the page draws whatever it is
given (`go2/web.py`, class `Classroom`). An announcement or an "I'm stuck"
button is a new item on the Python side, with no new JavaScript.

One click fetches it, hands it over, and says what arrived and to reopen any
notebook that was open. It never applies anything by itself: JupyterLab does
not reload an open notebook, so a file swapped under an open tab just looks
like nothing happened. Hosted labs never show the bar — there, the mentor's
sync is how material arrives. `GO2_COURSE_CHECK_EVERY=30` shortens the check
for a mentor watching a change land.

The simulator itself is *not* updated at launch — that is a 250 MB download,
and the start of a lesson is the worst time for one. When the class runs a
newer commit than the laptop, the launch says so and gives the install line.
Laptops installed before this existed need that one reinstall to start
pulling.

Then `go2-lab` applies the same rules as the server's `go2-student.sh sync`,
every launch. The material is handed over **read-only**, which makes the rule short:
the example is replaced with the current version every time, in place, under
its own name. Files the student made themselves are never looked at.

Read-only is what keeps the folder clean. A student duplicates the example
(right-click → **Duplicate** in JupyterLab) and edits and runs their cells in
that copy, which has a name we will never touch; the example stays the
example, so a fix lands in the same file instead of adding one. They can run
every cell in the read-only copy too — Jupyter only refuses to save it.

Where a student does own the example — a work folder seeded before the lock
existed, or someone who went around it — their file moves aside rather than
standing in the way of the update, keeping its content and its write
permission:

```
[go2] your 01_hello_dog.ipynb is now 01_hello_dog.yours.ipynb; 01_hello_dog.ipynb is the current example again
```

This replaced two earlier designs. "Copy if absent" meant a fix never reached
anyone who had already started. Leaving their file alone and putting the new
version beside it — first as `.updated`, then dated to the minute once a
student who had worked in *that* copy stopped receiving updates — worked, and
buried the class in four near-identical notebooks with no way to tell which
one the lesson was. Copies left by that scheme are removed on the next sync
if nobody opened them; the ones a student worked in are theirs and stay.

What a lock cannot fix: **JupyterLab does not reload a notebook that is
already open**, so a student with the tab open sees yesterday's lesson until
they close and reopen it. The notebook says so at the top, and `sync` prints
what it changed.

The comparison is against `.go2-seed` in the work folder — a copy of exactly
what was last handed over. Without it there is no way to tell an edited file
from an untouched one, which is why the old rule ("copy if absent") meant a
fix never reached anyone who had already started.

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

The terminal it runs in is the **Go2 Lab window** — it titles itself that, so
it is findable among a dozen others. Two things it does because the URL carries
a per-launch token and nobody is going to retype it:

- **Press Return** in that window to reopen the lab in the browser. Closing the
  tab is not a reason to restart the lab. If a tab is already open it says so
  first — the service knows, because the split page polls `/frame` constantly,
  so a fetch in the last few seconds means someone is watching. It still
  opens, since raising the browser is half of why Return gets pressed.
- **Closing the window stops the lab**, on purpose. macOS sends `SIGHUP`, which
  the launcher turns into the same shutdown Ctrl-C does, so JupyterLab and the
  simulator go with it rather than lingering on their ports.

### Which build is this?

There is no build server: the package is built on the student's own machine
from a tarball of whatever `main` held at that moment, so its version number
is the same string for every build ever made. The installer therefore writes
`go2/_build.py` into the installed copy — a timestamp to the second, and a
short hash of the exact tarball it built from. The timestamp orders builds;
the hash identifies the code, so two students who installed minutes apart from
the same commit carry the same hash and one who installed before a fix does
not.

```console
$ go2-lab --version
go2-lab build 20261112-192953Z
  source  b3ea24353093  from https://github.com/.../main.tar.gz
  python  3.12.16  (/Users/myao/.go2/venv/bin/python)
  on      Darwin 27.0.0 arm64
```

It is also on the launch banner, at the top of `--check`, and dimly at the
bottom of the viewer panel — where a student is already looking when they ask
for help. A source checkout has no stamp and says `source checkout` rather
than inventing one.

### Known constraints

- **Intel Macs** get MuJoCo 3.10 — the last release with x86_64 macOS wheels.
  3.11 onward is Apple Silicon only on the Mac, and 4.x moved the binary into
  `mujoco-core`, which is arm64 only. The installer reads the architecture and
  pins accordingly, so a 2019 Intel Mac runs the whole lab; it is slower.
  It never falls back to building MuJoCo from source — there is no supported
  way to do that on a laptop — so it steps back to a version with a wheel
  instead.
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
| `dog.say_again()` | Play the last clip once more |
| `dog.hush()` | Stop the sound that is playing. The clip is kept |
| `dog.listen(seconds)` | Record from the watching browser's microphone; returns the clip id |
| `dog.reset()` | Back to the start: origin, stopped, standing |
| `dog.stand()` | Stand still on all four legs |
| `dog.sit()` | Lower the body to the ground |
| `dog.backflip()` | One backwards somersault, then stand. Waits until it lands |
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
- `backflip()` is an animation of the same kind, so it cannot under-rotate or
  land badly. On the real robot it is the firmware's own trick (sport API
  2043) and genuinely dangerous.
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
  course_pack/        the bundled course: course.toml + units/ -- copied into each
                      student's folder, hosted or local, and the only copy
install/              laptop installer (mac.sh), the download zip, Gatekeeper note
deploy/               hosted mode: Dockerfile, compose, go2-student.sh
docs/                 DESIGN.md (why it is built this way), STEP2-REAL-ROBOT.md
tests/                pytest suite, headless
```

The only local change to the vendored model is a forward-facing `head` camera
used by `camera()`; it is marked in `go2/assets/go2.xml`. Upstream copies are
kept as `LICENSE` and `UPSTREAM_README.md` for diffing on upgrade.

## Status

The reasoning behind all of this is in [docs/DESIGN.md](docs/DESIGN.md); the
plan for the next step is in
[docs/STEP2-REAL-ROBOT.md](docs/STEP2-REAL-ROBOT.md). Work in progress is
tracked as issues.

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

See [docs/STEP2-REAL-ROBOT.md](docs/STEP2-REAL-ROBOT.md) for the plan, and
`go2/backends/real.py` for the seam it fills in.

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
