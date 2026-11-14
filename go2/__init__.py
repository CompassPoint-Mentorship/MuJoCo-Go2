"""Go2: one Python API for a simulated or real Unitree Go2.

    from go2 import Go2

    with Go2(sim=True) as dog:
        dog.stand()
        dog.move(vx=0.4)
        dog.wait(3)
        dog.stop()

Names are resolved on first use (PEP 562) rather than imported here, so that
`python -m go2.course` -- handing out notebooks -- does not drag in numpy and
mujoco to copy some files. That is not a micro-optimisation: the course tool
is the one piece of this package that runs somewhere the simulator is not
installed, including inside the classroom container, and a hard import here
made `go2-course sync` fail there with `No module named 'numpy'`.

`from go2 import Go2` costs exactly what it always did; it is the imports
nobody asked for that are gone.
"""

__version__ = "1.0.0"
__all__ = ["Go2", "Pose", "Velocity", "MAX_VX", "MAX_VY", "MAX_WZ"]

_LAZY = {
    "Go2": ".api",
    "MAX_VX": ".api",
    "MAX_VY": ".api",
    "MAX_WZ": ".api",
    "Pose": ".backends.base",
    "Velocity": ".backends.base",
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'go2' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__ + ["__version__"])
