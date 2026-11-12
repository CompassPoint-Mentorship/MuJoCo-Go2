# Design

The reasoning behind the code, collected in one place. Until now it lived in
module docstrings and commit messages, which is fine when you already know
where to look and useless when you do not.

This document is about *why*. For what the commands do, read
[README.md](../README.md); for running a class, [deploy/README.md](../deploy/README.md);
for the next thing to be built, [STEP2-REAL-ROBOT.md](STEP2-REAL-ROBOT.md).

---

## 1. The one promise

Everything here exists to keep a single sentence true:

> A student writes their code once, against a simulated dog, and the same code
> drives the real robot with one argument changed.

```python
dog = Go2(sim=True)     # a simulator on this laptop
dog = Go2(sim=False)    # a 15 kg robot over WiFi
```

Every design decision below follows from protecting that sentence. Anything
that makes the simulator *easier* at the cost of making simulated code and real
code diverge is the wrong trade, because the divergence is discovered by a
fourteen-year-old standing in a room with an actual robot.

Two consequences that look like bugs and are not:

- **Commands persist.** `move(vx=0.4)` walks until something says otherwise.
  The real robot behaves this way — it has no idea when your cell finished —
  so the simulator does too.
- **`move()` is a request, not a guarantee.** In simulation it happens to be
  tracked exactly. Student code must never rely on that, which is why the
  course material teaches `pose()` feedback loops with stall guards from the
  second unit onward.

## 2. Three products, three clocks

The repository holds three things that change at different speeds. Tying them
together means the slowest clock governs all of them.

| Part | Changes | If it were coupled |
|---|---|---|
| **Engine** — the `Go2` API and its backends | monthly | — |
| **Delivery** — lab launcher, service, containers, installer | per term | a syllabus typo would need a release |
| **Course** — notebooks and units | the morning somebody finds a typo | …and on the hosted side, a container rebuild |

So the engine knows nothing about any particular course. It knows how to find
a *pack* and hand it out (§7). The course is data, not code.

## 3. Shape

```
                    student code
                         │
                    go2.Go2  ........... clamps, the real-robot interlock
                         │
                 Backend (ABC)  ........ the only seam that matters
          ┌──────────────┼──────────────┐
      SimBackend    RemoteBackend    RealBackend
       MuJoCo        HTTP client      WebRTC  (step 2)
       in-process    of a service     to the robot
                         │
                 go2.service ........... one process owns one dog
                         │
                   go2.web ............. viewer, split page, JSON/JPEG API
```

`go2/backends/base.py` is the whole contract: `say`, `reset`, `stand`, `sit`,
`move`, `stop`, `pose`, `velocity`, `camera`, `close`. Adding a new way to
drive a dog — hardware, a different simulator, a recording for a test — means
implementing that and nothing else.

The contract is deliberately *small and high level*. It carries no joint
angles, no torques, no gait parameters. Not because those are uninteresting,
but because the Go2 Pro cannot accept them (§6) — an API with `set_joint_torque`
on it would be a promise the hardware breaks.

**Student code never imports from `go2.backends`.** That rule is what lets
every layer below `Go2` be rewritten without touching a notebook.

## 4. Safety is structural, not procedural

Three mechanisms, each cheap and each load bearing:

- **The real-robot interlock.** `Go2(sim=False)` raises `PermissionError`
  unless `GO2_ALLOW_REAL=1`. The hosted image and compose file hard-set it to
  `0`. A hosted notebook can be opened from anywhere, and the real backend
  drives 15 kg of robot that may have nobody beside it, so hardware is opt-in
  per machine by someone who knows a human is present with the remote in hand.
  `test_real_mode_is_disabled_by_default` guards it.
- **Velocity clamps.** `MAX_VX`, `MAX_VY`, `MAX_WZ` are applied in `Go2.move`,
  above every backend, so a typo in an assignment cannot ask the real dog for
  something alarming. Clamping in the API rather than per backend means a new
  backend inherits the limits by default instead of having to remember them.
- **The token is the login.** Hosted labs and the local launcher both mint a
  token and treat the link as a password. The local one is fresh per launch,
  which costs the student nothing because the launcher opens the browser.

## 5. Who owns the dog

The most consequential decision in the project, and the least obvious.

A dog can live **in the notebook kernel** or **in a long-lived service**. We do
both, chosen by `GO2_SIM_URL`:

| | In-kernel (`SimBackend`) | Service (`RemoteBackend`) |
|---|---|---|
| Set by | a plain `pip install` | hosted containers, and `go2-lab` |
| Restart the kernel | dog is destroyed | dog keeps its position |
| Before any cell runs | nothing to show | a live dog to show |
| Two notebooks | two unrelated dogs | the same dog |

The service model is what makes the split page work: the student opens one
link and the dog is already there, walking or standing where they left it.
That single property is worth the costs, which are real and are documented for
students rather than hidden:

- state persists across a kernel restart, so experiments should begin with
  `dog.reset()`;
- the dog keeps walking if a cell raises;
- `pose()` costs an HTTP round trip, so control loops sleep rather than spin.

## 6. The walking problem

MuJoCo gives an accurate Go2 *body* — 12 motors, mass, contact — and no idea
how to walk. Unitree's walking controller is closed firmware on the physical
robot and cannot be copied into a simulator. Load the bare model, command
nothing, and the dog folds onto the floor at 7.7 cm.

So the simulator supplies walking itself, currently as the cheapest honest
version: `gait.py` plays a kinematic trot animation while `backends/sim.py`
integrates the commanded velocity into the world pose. Stated plainly, because
nobody should present this as something it is not:

- the dog **cannot** fall, trip or slip;
- commanded velocity is tracked **exactly**, with no error;
- obstacles **do** block it — MuJoCo collision detection runs every tick and
  motion into an `obs_*` geom is refused, while the turn is kept so a student
  can rotate away from a wall rather than being stuck against it.

That is enough for navigation, perception and behaviour work, which is what
the course is about. The upgrade path (pretrained policy, then an in-house
one) touches `gait.py` and `sim.py` only — see the README and
[STEP2-REAL-ROBOT.md](STEP2-REAL-ROBOT.md) for where that sits in the order of
work. On the real dog the problem does not exist: Unitree's controller is
already there and does the walking.

## 7. Course packs

A pack is a directory: `course.toml` (id, title, version, units in order) and
`units/` (the files a student gets). Resolution, identical in the launcher and
on the server:

```
--course <path>   >   $GO2_COURSE   >   ~/.go2/courses/<id>   >   the bundled pack
```

The bundled pack ships inside the wheel so a laptop install has something to
open with no network and no second step. It is the default, not a special
case.

**Handing out is the part worth being careful about.** The rules live in
`go2/course.py` once, and both `go2-lab` and `deploy/go2-student.sh sync` call
them, because two implementations of "replace what nobody touched" would drift
apart within a term.

| The student's file | What happens |
|---|---|
| missing | copied in |
| byte-identical to the seed — never edited | replaced |
| edited, or older than the seed mechanism | left alone; the new version lands beside it as `<name>.updated.<ext>` |

`.go2-seed` holds a copy of exactly what was last handed over. Without it
there is no way to tell an edited file from an untouched one, and the only
safe rule left is "never overwrite" — which is how a fix stops reaching
anyone who has already started.

## 8. Concurrency rules

Two hard-won constraints, both of which crash rather than raise, and both of
which have tests:

- **One lock around all of `MjData`.** MuJoCo's data is plain memory with no
  internal locking. Stepping it on the sim thread while another thread calls
  `mj_forward` (reset) or `mjv_updateScene` (render) is a data race that
  **segfaults the process**. Everything that touches it goes through one
  re-entrant lock, and `reset()` is applied *by* the sim thread rather than by
  the caller. Guarded by `test_reset_and_render_under_concurrent_load`.
- **One thread owns the EGL context.** `eglMakeCurrent` from a second thread
  fails with `EGL_BAD_ACCESS`, and the notebook's `show()` runs on the kernel
  thread while the web viewer renders on its own. All rendering is funnelled
  through a single-worker pool that owns the context, and the renderer is
  freed on that same thread.

## 9. Delivery

Same lab, three ways in:

| | Hosted | Laptop | Source |
|---|---|---|---|
| Student installs | nothing | one `curl` line | git + venv |
| Where it runs | one container each | `~/.go2` | a clone |
| The UI | split page | **the same split page**, on `127.0.0.1` | same |
| Needs the network | yes | only to install | yes |

The local viewer is a browser page rather than MuJoCo's native window on
purpose: one UI to learn instead of two; `localhost` is a secure context so
the microphone works with no certificate; and a page has no main-thread rule,
so one code path covers macOS and Ubuntu rather than `mjpython` and not. The
native window stays one command away — `go2-run` re-execs under `mjpython`
on macOS so nobody has to remember to.

Two installer decisions that are easy to undo by accident:

- **Nothing system-wide, no password.** Python comes from `uv`; reaching for
  the system `python3` on a clean Mac pops the developer-tools dialog, which
  is a dead end for a fourteen-year-old. The package arrives as a tarball,
  not `git+https`, because a clean Mac has no git either.
- **The `.app` is written on the student's machine**, so it carries no
  quarantine attribute and Gatekeeper never challenges it. A downloaded `.app`
  would be blocked outright short of paying Apple to notarise it.

## 10. Knowing which code you are looking at

There is no build server: the package is built on the student's own machine
from a tarball of whatever `main` held at that moment, so the version string
is identical for every build ever made. The installer therefore writes
`go2/_build.py` into the *installed* copy — a timestamp to the second and a
short hash of the exact tarball. The timestamp orders builds; the hash
identifies the code.

It appears on the launch banner, in `--check`, in `--version`, and dimly at the
bottom of the viewer panel — where a student is already looking when they ask
for help. A source checkout has no stamp and says `source checkout` rather
than inventing one.

## 11. When something breaks

The policy is **supervise, recover, and say so out loud**.

- The simulator is supervised in both delivery modes — by `_Supervisor` in
  `desktop.py` and by `supervise_simulator` in `deploy/entrypoint.sh`. A dead
  simulator leaves a frozen panel and a notebook full of
  `ConnectionRefusedError` for a student who did nothing wrong. Restarting
  loses the dog's position, which is recoverable; staying dead is not.
  Restarts are logged and counted, so a dog that mysteriously teleports home
  has an explanation in the log.
- Failure must be *visible*. The container health check probes both halves —
  Lab and the simulator's `/state` — because probing only Lab once let a
  container sit "healthy" for thirteen hours with a dead simulator. The split
  page covers itself when Jupyter stops answering at the network level, rather
  than leaving JupyterLab to offer a reconnection that cannot happen.
- Shutdown says what is safe to close. A page cannot close a window it did not
  open, so the launcher prints where the notebooks are and what to close
  instead of leaving a dead terminal and a tab of errors to be puzzled over.

## 12. Non-goals

Worth writing down, because each has been considered and declined:

- **Dynamics accuracy, for now.** See §6. It is a stand-in and is labelled as
  one everywhere it could mislead.
- **Low-level joint control.** The Go2 Pro cannot accept it (no
  `unitree_sdk2`, no CycloneDDS, no onboard Jetson), so the API does not
  pretend to offer it.
- **A pip-installable course.** A student should never need a second install
  step, and a syllabus fix should not need a release.
- **Multi-robot.** One `Go2` is one dog. Several processes can each have one;
  nothing coordinates them.
- **Authentication on the bare viewer.** `python -m go2.web` binds all
  interfaces with no auth, like the Isaac Sim viewer beside it. It is a
  simulated dog on a local network; the README says so plainly and `--host
  127.0.0.1` is there for when it matters.

## 13. Where the roadmap lives

`README.md` → **Status**. Step 1 (API, MuJoCo backend, arena, tests) and
steps 5a–5c (browser viewer, hosted mode, laptop install) are done. Open:

- **Step 2** — the real Go2 Pro backend → [STEP2-REAL-ROBOT.md](STEP2-REAL-ROBOT.md)
- **Step 3** — a trained walking policy, so the sim looks right
- **Step 4** — assignments and a student setup guide

Tracked as issues on the repository.
