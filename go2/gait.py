"""Stand-in trot animation for the simulated dog.

WHY THIS EXISTS
---------------
MuJoCo simulates the Go2's *body* -- 12 leg motors, mass, contact -- but it has
no idea how to walk. Unitree's walking controller is closed firmware living on
the real robot, and it cannot be copied into a simulator. Load the bare model
and command nothing, and the dog simply folds onto the floor.

So in simulation we supply the walking ourselves. This module is the cheapest
honest version of that: a kinematic trot *animation*. It decides 12 joint angles
from a clock and a commanded speed. It does not balance, and it is not derived
from physics -- it just looks like a trot and keeps the feet near the ground.

That is deliberate. Student code talks to move()/pose()/camera(), and none of
those care how the legs got where they are. When a trained walking policy
replaces this file, student code does not change.

See README.md ("The walking problem") for the upgrade path.
"""

from __future__ import annotations

import math

# Nominal standing pose, per leg: (hip, thigh, calf) in radians.
# Matches the "home" keyframe shipped with the Menagerie model.
STAND = (0.0, 0.9, -1.8)

# Tucked-under pose used by sit().
SIT = (0.0, 1.45, -2.6)

# Leg order as MuJoCo reports it: front-left, front-right, rear-left, rear-right.
LEGS = ("FL", "FR", "RL", "RR")

# Trot: diagonal pairs swing together, so FL+RR are in phase and FR+RL are
# half a cycle behind.
PHASE_OFFSET = {"FL": 0.0, "FR": 0.5, "RL": 0.5, "RR": 0.0}

STRIDE_HZ = 2.2          # full gait cycles per second at full speed
REFERENCE_SPEED = 0.6    # m/s at which the stride reaches full amplitude
LIFT_THIGH = 0.38        # radians of thigh flexion at peak swing
LIFT_CALF = 0.55         # radians of calf flexion at peak swing
REACH = 0.22             # fore/aft thigh swing, radians


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def leg_angles(leg: str, t: float, speed: float) -> tuple[float, float, float]:
    """Joint angles (hip, thigh, calf) for one leg at time `t`.

    `speed` is the magnitude of the commanded body velocity in m/s. At zero it
    returns the standing pose exactly, so a stopped dog is perfectly still.
    """
    hip0, thigh0, calf0 = STAND
    amplitude = _clamp(speed / REFERENCE_SPEED, 0.0, 1.0)
    if amplitude <= 1e-6:
        return hip0, thigh0, calf0

    phase = (t * STRIDE_HZ * amplitude + PHASE_OFFSET[leg]) % 1.0
    angle = 2.0 * math.pi * phase

    # Swing during the first half of the cycle, stance during the second.
    swing = max(0.0, math.sin(angle))
    thigh = thigh0 - LIFT_THIGH * amplitude * swing + REACH * amplitude * math.cos(angle)
    calf = calf0 + LIFT_CALF * amplitude * swing
    return hip0, thigh, calf


def pose_vector(t: float, speed: float) -> list[float]:
    """All 12 joint angles, in the model's joint order."""
    out: list[float] = []
    for leg in LEGS:
        out.extend(leg_angles(leg, t, speed))
    return out


def stand_vector() -> list[float]:
    return list(STAND) * 4


def sit_vector() -> list[float]:
    return list(SIT) * 4


def body_bob(t: float, speed: float) -> float:
    """Small vertical oscillation so a walking dog does not glide rigidly."""
    amplitude = _clamp(speed / REFERENCE_SPEED, 0.0, 1.0)
    if amplitude <= 1e-6:
        return 0.0
    return 0.012 * amplitude * math.sin(4.0 * math.pi * STRIDE_HZ * amplitude * t)


# ---------------------------------------------------------------------------
# Tricks
# ---------------------------------------------------------------------------
#
# A backflip here is an ANIMATION, exactly like the trot above: the body is
# rotated through a full circle and the legs tuck, because the simulator has
# no walking controller and no jumping one either. Nothing is launched by
# pushing against the floor, so the dog cannot under-rotate, land badly or
# hurt itself.
#
# On the real robot this is firmware -- sport API id 2043 -- and it is a
# genuinely dangerous move: 15 kg of robot leaves the ground. Keeping both
# facts in one place is the point of this comment.

FLIP_SECONDS = 1.2       # how long the whole move takes
FLIP_HEIGHT = 0.35       # metres gained at the top of the arc
TUCK = (0.0, 1.70, -2.75)    # knees pulled in while airborne


def _smoothstep(x: float) -> float:
    """Ease in and out, so the rotation starts and ends at rest."""
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def backflip_pose(phase: float) -> tuple[float, float, list[float]]:
    """Body pitch, height gain and 12 joint angles, at `phase` in [0, 1].

    Phase 0 and phase 1 are both the standing pose, so the move begins and
    ends where the dog already was -- it is a loop, not a displacement.
    """
    phase = _clamp(phase, 0.0, 1.0)

    # One full turn backwards about the body's lateral axis. Negative is
    # nose-up, which is what makes it a BACK flip rather than a front one.
    pitch = -2.0 * math.pi * _smoothstep(phase)

    # An arc: nothing at the ends, highest in the middle.
    airborne = math.sin(math.pi * phase)
    lift = FLIP_HEIGHT * airborne

    # Tuck the legs in while off the ground, and put them back down for the
    # landing. Same blend as the arc, so the dog is most tucked at the top.
    angles: list[float] = []
    for _leg in LEGS:
        angles.extend((
            STAND[0],
            STAND[1] + (TUCK[1] - STAND[1]) * airborne,
            STAND[2] + (TUCK[2] - STAND[2]) * airborne,
        ))
    return pitch, lift, angles
