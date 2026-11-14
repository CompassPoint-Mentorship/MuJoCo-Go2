# JupyterLab + the go2 simulator, one container per student.
#
# The student needs nothing installed: they open a link and get an editor, a
# terminal and the dog rendering inline in notebook cells.
#
# Rendering is offscreen via EGL on the host GPU (~0.3 ms/frame, ~150 MB VRAM
# per container). To run with no GPU at all, build with MUJOCO_GL=osmesa and
# drop the nvidia runtime from docker-compose.yml -- software rendering is
# slower but needs no driver.
FROM python:3.12-slim

# EGL/GL loaders. The NVIDIA container toolkit injects the driver's own
# libEGL_nvidia at runtime; these are the vendor-neutral loaders it plugs into.
# libosmesa6 is the software fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libegl1 libgl1 libglib2.0-0 libosmesa6 \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Ask the toolkit for graphics capability, not just compute -- without
# "graphics" there is no EGL and rendering fails.
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility \
    MUJOCO_GL=egl \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Real-robot mode stays OFF in the image. A hosted notebook can be opened from
# anywhere, and Go2(sim=False) drives a physical robot. Never set
# GO2_ALLOW_REAL here; it belongs only on a machine where a human is present.
ENV GO2_ALLOW_REAL=0

RUN useradd --create-home --uid 1000 student
WORKDIR /opt/go2

# Dependencies first so edits to our code do not re-resolve the world.
COPY pyproject.toml README.md ./
# jupyterhub provides jupyterhub-singleuser, which is how a lab runs when the
# hub starts it. Pinned to the hub's own version (deploy/hub/Dockerfile): the
# two talk over an internal API, and a mismatch fails at sign-in.
RUN pip install --no-cache-dir \
        "mujoco>=3.2,<5" "numpy>=1.24" "pillow>=10" \
        jupyterlab notebook ipywidgets \
        jupyter-server-proxy "jupyterhub==6.2.0"

COPY go2 ./go2
RUN pip install --no-cache-dir -e . \
    && chown -R student:student /opt/go2

# Student-visible working area. Notebooks and their own scripts live here and
# are bind-mounted from the host, so work survives a container rebuild.
RUN mkdir -p /home/student/work && chown -R student:student /home/student
USER student
WORKDIR /home/student/work

EXPOSE 8888

# -k because the certificate is self-signed; this probe is inside the
# container, so there is nothing to authenticate against anyway.
# Both halves must answer. Probing only Lab once let a container sit "healthy"
# for 13 hours with a dead simulator: students saw a 500 on the split page
# while docker insisted all was well.
# Under the hub, Jupyter lives at /labs/user/<name>/ rather than / -- the
# prefix arrives as JUPYTERHUB_SERVICE_PREFIX -- and its /api answers without
# a login, which is all a liveness probe needs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD { curl -fsSk "https://localhost:8888/lab" >/dev/null \
       || curl -fsS "http://localhost:8888/lab" >/dev/null \
       || curl -fsS "http://localhost:8888${JUPYTERHUB_SERVICE_PREFIX:-/}api" >/dev/null; } \
     && curl -fsS "http://127.0.0.1:${GO2_SIM_PORT:-8399}/state" >/dev/null || exit 1

# The simulator runs as its own service; notebooks are clients of it.
# jupyter-server-proxy exposes it at /proxy/8399/, same-origin and behind the
# same token, so the split page can frame both Lab and the viewer.
ENV GO2_SIM_PORT=8399 \
    GO2_SIM_URL=http://127.0.0.1:8399

COPY --chown=student:student deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
# System-wide, so it applies however the lab is started: let the split page
# frame JupyterLab (see the file for why JupyterHub's default breaks that).
COPY deploy/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
