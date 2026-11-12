"""Drive a square using only pose() feedback.

This is the first real assignment shape: command a velocity, watch pose(),
decide when to stop. The same code would run on the real dog.

Note the stall guard in walk_distance(). The dog can be blocked -- by an
obstacle here, by a wall or a person on the real robot -- and a loop that waits
for a distance that will never arrive hangs forever. Always bound the wait.
"""

import math
import time

from go2 import Go2

SIDE = 1.2        # metres -- keeps clear of the arena obstacles
SPEED = 0.4       # m/s
TURN = 0.8        # rad/s


def walk_distance(dog, metres, timeout=None):
    """Walk forward until `metres` have been covered, or give up.

    Returns True if the distance was reached, False if the dog stalled.
    """
    if timeout is None:
        timeout = metres / SPEED * 3.0 + 2.0

    start = dog.pose()
    deadline = time.monotonic() + timeout
    dog.move(vx=SPEED)
    try:
        while True:
            now = dog.pose()
            travelled = math.hypot(now.x - start.x, now.y - start.y)
            if travelled >= metres:
                return True
            if time.monotonic() > deadline:
                print(f"  ! stalled after {travelled:.2f} m -- something is in the way")
                return False
            dog.wait(0.05)
    finally:
        dog.stop()


def turn_by(dog, radians, timeout=10.0):
    """Turn by a relative angle, accumulating across the -pi/+pi wrap."""
    deadline = time.monotonic() + timeout
    dog.move(wz=TURN if radians > 0 else -TURN)
    try:
        turned = 0.0
        previous = dog.pose().yaw
        while abs(turned) < abs(radians):
            if time.monotonic() > deadline:
                print("  ! turn timed out")
                return False
            current = dog.pose().yaw
            turned += math.atan2(
                math.sin(current - previous), math.cos(current - previous)
            )
            previous = current
            dog.wait(0.02)
        return True
    finally:
        dog.stop()


with Go2(sim=True) as dog:
    dog.stand()
    for corner in range(4):
        print(f"side {corner + 1}: starting from {dog.pose()}")
        walk_distance(dog, SIDE)
        turn_by(dog, math.pi / 2)
    print("back home-ish:", dog.pose())
