# Step 2 — the real Go2 Pro, over WebRTC

An implementation plan for `go2/backends/real.py`, which today is a docstring
and a `NotImplementedError`.

The seam is already there and already tested, so this is not a redesign: it is
filling in one class behind an interface that three other implementations
already satisfy. What follows is the order to do it in, the protocol facts we
already know, and the decisions that must be made with the robot in front of
you rather than at a desk.

> **Scope.** Nothing in this document changes `go2/api.py`, the course
> material, or any student's code. If it starts to, something has gone wrong —
> see [DESIGN.md §1](DESIGN.md#1-the-one-promise).

---

## 1. What the hardware will and will not do

Ours is a **Go2 Pro**. The constraints are the plan's foundation:

| | Pro |
|---|---|
| `unitree_sdk2` / CycloneDDS low-level control | **No** — an EDU feature |
| WebRTC (the channel the phone app uses) | **Yes** — high-level commands, camera, LiDAR |
| Onboard Jetson | **No** — code runs on a laptop and drives the robot over WiFi |
| Concurrent clients | **One.** The phone app must be disconnected first |

This is why `Backend` carries `move/stand/sit/stop/pose/camera/say` and no
joint-level anything: the API never promises what this robot cannot do.

It also means Unitree's walking controller does the walking, so
[the walking problem](DESIGN.md#6-the-walking-problem) simply does not arise
here. Step 2 and step 3 are independent; neither blocks the other.

## 2. Prior art we already own

`/srv/go2-pocket` (`coderplay/go2-pocket`, MIT, from `legion1581/unitree_ui`)
is a working TypeScript client for this exact robot, with the protocol written
down. It is the reference, not a dependency — but it means almost nothing here
has to be discovered by packet capture.

| Need | Where it is documented |
|---|---|
| Handshake, LAN/AP/remote modes, AES-128 key | `docs/connection.md` |
| Topic table and sport API ids | `src/protocol/topics.ts` |
| Request envelope | `src/protocol/data-channel.ts` |
| Action/mode ids as tested on firmware | `docs/control.md` |
| Telemetry fields (battery, IMU, motors) | `docs/status.md` |

On the Python side the candidate transports are the community projects
`go2_webrtc_connect` / `unitree_webrtc_connect` plus `aiortc`. They are
community software, not Unitree's, and a firmware update can break them —
which is the argument for keeping `real.py` **thin**, so a fix stays cheap.

## 3. Transport

Three connection modes, in the order they matter to us:

1. **Local network (STA-L)** — robot and laptop on the same WiFi. Discovery is
   a UDP multicast to `231.1.1.1:10131`. This is the classroom case.
2. **Access point** — join the robot's own hotspot; it is always
   `192.168.12.1`. The fallback when the venue's WiFi is hostile, and worth
   supporting precisely because that happens on the day.
3. **Remote**, via Unitree's relay — needs a cloud session. Out of scope: a
   robot drivable from the internet is the thing our safety model exists to
   prevent.

**Firmware ≥ 1.1.15 requires a per-device AES-128 key** to complete the SDP
exchange (the signaling reply returns `data2=3`; older firmware returns
`data2=2` and a static key is handled transparently). The key is per device
and stable across re-pairings. Ours must come from configuration — an env var
or a file, never a literal in the repository — and the failure message when it
is missing or wrong has to say *which* of those two it was, because they look
identical from the outside.

## 4. The mapping

Requests go to a topic with an envelope of
`{header: {identity: {id, api_id}}, parameter: "<json>"}`. The ids below are
from `src/protocol/topics.ts`, tested against Go2 firmware v1.1.11.

| `Backend` method | Topic | api_id | Parameter | Notes |
|---|---|---|---|---|
| `move(vx, vy, wz)` | `rt/api/sport/request` | `1008` Move | `{"x": vx, "y": vy, "z": wz}` | see §5 on persistence |
| `stop()` | `rt/api/sport/request` | `1003` StopMove | `{}` | stays standing |
| `stand()` | `rt/api/sport/request` | `1002` BalanceStand | `{}` | **verify**: `1004` StandUp is "rise from lying"; `1006` RecoveryStand is the get-up-after-a-fall |
| `sit()` | `rt/api/sport/request` | `1009` Sit | `{}` | `1010` RiseSit comes back up — `stand()` after `sit()` must do the right thing |
| `pose()` | subscribe `rt/utlidar/robot_pose`, `rt/lf/sportmodestate` | — | — | odometry; it drifts (§6) |
| `velocity()` | last commanded | — | — | mirror the simulator: report what was asked for |
| `camera()` | WebRTC video track | — | — | `head` only; `chase` must raise a clear error |
| `say()` | `rt/api/audiohub/request` | upload + play | — | the Pro has a speaker; no EDU hardware needed |
| `reset()` | — | — | — | **has no hardware meaning** (§6) |
| `close()` | `1003` StopMove, then tear down | — | — | **never** send `1001` Damp: it drops the dog |

Two entries deserve more than a row.

## 5. Does a command persist?

The simulator's contract is that `move()` runs until told otherwise, and that
contract came *from* the real robot. But a high-level controller reached over
a lossy WiFi link usually has a watchdog that stops the robot if commands stop
arriving — the phone app streams joystick state continuously rather than
sending one command.

**This must be measured before anything else is built on top of it.** Command
the robot forward once, do not send anything else, and time how long it walks.
The answer picks the design:

- *It persists* → the backend forwards commands one for one, like the sim.
- *It times out* (the likely case) → the backend owns a repeater thread that
  re-sends the current command at a fixed rate, so the student-visible
  contract is unchanged. That thread then **is** the safety watchdog: when the
  connection drops, it stops repeating, and the robot's own timeout stops it.

Either way the student sees "commands persist". The difference is entirely
inside `real.py`, which is the point of the seam.

## 6. `pose()` and `reset()` are honest, not convenient

In simulation `pose()` is ground truth and `reset()` teleports the dog to the
origin. Neither is possible on hardware, and faking them would teach exactly
the wrong lesson.

- **`pose()`** comes from odometry, which drifts — more on carpet, more after
  turns. It stays the same `Pose(x, y, yaw)` in the same world frame, with the
  origin taken at connect. The docstring must say it drifts; unit 02 already
  teaches the feedback loop and stall guard that make drift survivable, which
  is why that unit is written the way it is.
- **`reset()`** cannot move a robot across a room. It should re-zero the
  software origin and bring the dog to a stand, and it must **say** that it
  did not teleport anything. A student who calls `reset()` between experiments
  and finds the dog two metres from where they expected has been misled by us.

## 7. Safety

The interlock exists already: `Go2(sim=False)` raises `PermissionError`
without `GO2_ALLOW_REAL=1`, the hosted image hard-sets it to `0`, and a test
guards it. Step 2 adds the things that only matter once the backend is real:

- **A human with the remote, in the room, every time.** Hardware runs in a
  booked slot on a machine where that is true. Never on the teaching server.
- **Lower limits on hardware.** `MAX_VX/VY/WZ` are tuned for a simulator that
  cannot hurt anyone. The real backend should start well below them and only
  rise with a reason.
- **Stop on disconnect**, by construction — §5's repeater, plus an explicit
  stop in `close()` and in the context manager's `__exit__`.
- **Fail closed and loudly.** Every error path stops the dog first and
  explains second.
- **The phone app must be disconnected.** One client at a time; the error when
  it is not says so in those words, because that is the first thing anyone
  will hit.

## 8. Testing without a robot

Hardware cannot be in CI, which does not mean the backend is untestable:

- A **fake transport** that records published envelopes lets the whole mapping
  table in §4 be asserted — topics, api ids, parameter shapes, clamping,
  `chase` rejected, `close()` sending StopMove and never Damp.
- The **interlock** test already exists and must keep passing.
- A **hardware checklist** in this file, run by a person: connect, stand, walk
  a metre, stop, sit, stand, pose drift over a square, a frame from the
  camera, say a clip, pull the WiFi and confirm the dog stops.

## 9. Milestones

Each one ends somewhere worth stopping.

| | Milestone | Done when |
|---|---|---|
| **M0** | Measure the watchdog (§5) and the firmware version | the persistence question is answered in writing |
| **M1** | Connect and disconnect cleanly, LAN and AP, with the AES key from config | `RealBackend(ip=...)` constructs and tears down with no robot motion |
| **M2** | Motion: `move`, `stop`, `stand`, `sit` + the repeater/watchdog | a metre walked and stopped, with the remote in hand |
| **M3** | `pose()` and `velocity()` from telemetry | unit 02's square runs unmodified on the real dog |
| **M4** | `camera()` from the video track | unit 03 runs unmodified |
| **M5** | `say()` via audiohub | a student's recorded clip plays from the robot |
| **M6** | Docs, limits, the hardware checklist, the booked-slot procedure | a mentor who did not write this can run a hardware session |

M3 and M4 are the ones that prove the promise: **the course units run
unchanged**. If a unit needs editing to work on hardware, that is a bug in
this backend, not in the unit.

## 10. Open questions

- `stand()` → `BalanceStand` (1002) or `StandUp` (1004)? And does `stand()`
  after `sit()` need `RiseSit` (1010) instead? Decide on hardware (M2).
- Which Python WebRTC client, and do we vendor it? It is community software
  that a firmware update can break.
- Does the video track give frames fast enough for `camera()` to feel like the
  simulator's, or does it need a frame cache?
- Where does the AES key live for a class — one per robot, in a file beside
  the booked-slot procedure?
- Battery and temperature are on `rt/lf/lowstate`. Worth surfacing as a
  `dog.battery()`, or does that widen the API for one platform?
