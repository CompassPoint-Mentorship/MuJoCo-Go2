"""Give JupyterLab the class's stylesheet.

JupyterLab 4.1 added one supported way to restyle the application without
writing an extension: a file at `{jupyter_config_dir}/custom/custom.css`,
served only when `LabApp.custom_css` is on. Both launchers -- the laptop one
in `desktop.py` and the container's `entrypoint.sh` -- call `install()` just
before starting the lab, so a student gets the same chrome either way.

It is copied rather than symlinked: the config directory outlives the
install (a venv can be rebuilt under it), and a dangling symlink there would
make the lab serve a 404 for its own stylesheet.
"""

from __future__ import annotations

import pathlib
import shutil

SOURCE = pathlib.Path(__file__).resolve().parent / "assets" / "lab.css"


def config_dir() -> pathlib.Path:
    """Where Jupyter reads config from, without importing jupyter_core early."""
    try:
        from jupyter_core.paths import jupyter_config_dir
    except Exception:  # noqa: BLE001 - no jupyter here means nothing to style
        return pathlib.Path.home() / ".jupyter"
    return pathlib.Path(jupyter_config_dir())


def target() -> pathlib.Path:
    return config_dir() / "custom" / "custom.css"


def install() -> pathlib.Path | None:
    """Put the stylesheet where JupyterLab looks. Returns the path, or None.

    Never raises: a lab that starts in JupyterLab's colours is a working
    lab, and a class must not fail to start over a stylesheet.
    """
    try:
        if not SOURCE.is_file():
            return None
        dst = target()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE, dst)
        return dst
    except OSError:
        return None


if __name__ == "__main__":       # how entrypoint.sh calls it
    print(install() or "")
