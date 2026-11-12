"""Go2: one Python API for a simulated or real Unitree Go2.

    from go2 import Go2

    with Go2(sim=True) as dog:
        dog.stand()
        dog.move(vx=0.4)
        dog.wait(3)
        dog.stop()
"""

from .api import MAX_VX, MAX_VY, MAX_WZ, Go2
from .backends.base import Pose, Velocity

__version__ = "0.1.0"
__all__ = ["Go2", "Pose", "Velocity", "MAX_VX", "MAX_VY", "MAX_WZ"]
