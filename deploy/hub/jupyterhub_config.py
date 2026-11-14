"""JupyterHub for the online labs: one container per student, under /labs/.

Everything the hosted labs need, and nothing they don't:

* **Under /labs/, beside the site, not in front of it.** The gateway sends
  /labs/ here and everything else straight to the classroom site. The trial
  first put this hub's proxy in front of everything, and that was wrong: the
  online labs are the backup, and the backup must not be able to take down
  the install line, the student pages, or the course pull every laptop does
  at launch. (--default-target still passes the rest of the site through, so
  the trial port shows the whole thing on its own.)
* **One sign-in.** HandoffAuthenticator accepts a signed pass from the
  classroom site and nothing else; the hub never shows a login page, and lab
  URLs never carry a token.
* **The same lab.** DockerSpawner starts the go2-lab image students already
  use, with the GPU attached and the same student-work/<name> folder, so a
  student's files are where they always were.
* **Only while used.** The idle culler stops a lab nobody has touched for an
  hour; it starts again the next time the student opens it.
"""

import os
import sys

import docker

sys.path.insert(0, "/srv/go2-hub")
c = get_config()  # noqa: F821 - provided by JupyterHub

CLASSROOM_URL = os.environ.get("CLASSROOM_URL", "http://192.168.1.215:8500").rstrip("/")
WORK_ROOT = os.environ.get("WORK_ROOT", "/srv/MuJoCo-Go2/student-work")
PUBLIC_PORT = int(os.environ.get("HUB_PUBLIC_PORT", "8600"))
NETWORK = os.environ.get("DOCKER_NETWORK_NAME", "go2-hub")

# ------------------------------------------------------------ where it lives
# bind_url alone: its path becomes base_url. Setting base_url as well makes
# JupyterHub rebuild bind_url from the default port 8000 -- silently, apart
# from a warning -- which is exactly what the first trial run did.
c.JupyterHub.bind_url = f"http://0.0.0.0:{PUBLIC_PORT}/labs/"
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"
c.JupyterHub.hub_connect_url = "http://go2-hub:8081"    # as the lab containers see it
c.ConfigurableHTTPProxy.command = [
    "configurable-http-proxy",
    f"--default-target={CLASSROOM_URL}",
    # Leave the gateway's X-Forwarded-* alone. By default this proxy appends
    # its own, so X-Forwarded-Proto arrives as "https,http" and the hub --
    # which reads the last value -- thinks the visitor came over plain http.
    "--no-x-forward",
]
c.JupyterHub.cookie_secret_file = "/srv/jupyterhub/data/jupyterhub_cookie_secret"
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/data/jupyterhub.sqlite"

# ------------------------------------------------------------ who
c.JupyterHub.authenticator_class = "go2_handoff.HandoffAuthenticator"
c.HandoffAuthenticator.secret = os.environ.get("GO2_HUB_SECRET", "")
c.HandoffAuthenticator.classroom_url = "/student/lab"
# Who may sign in is decided by the classroom site, which only writes a pass
# for a signed-in student on its roster; the hub trusts the signature.
c.Authenticator.allow_all = True

# ------------------------------------------------------------ the lab
c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = "go2-lab:latest"
# Named here because the lab image has an ENTRYPOINT and no CMD, and
# DockerSpawner otherwise reads the image's CMD -- which then does not exist.
# The entrypoint starts the simulator and hands over to this.
c.DockerSpawner.cmd = ["jupyterhub-singleuser"]
c.DockerSpawner.network_name = NETWORK
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.remove = True              # the container goes; the work folder stays
c.DockerSpawner.name_template = "go2-hub-{username}"
c.DockerSpawner.notebook_dir = "/home/student/work"
c.DockerSpawner.volumes = {f"{WORK_ROOT}/{{username}}": "/home/student/work"}
c.DockerSpawner.extra_host_config = {
    # EGL rendering needs the GPU's graphics capability, which the image asks
    # for in NVIDIA_DRIVER_CAPABILITIES.
    "device_requests": [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])],
}
c.DockerSpawner.environment = {
    "MUJOCO_GL": "egl",
    "GO2_ALLOW_REAL": "0",                      # never 1 on a shared server
    "GO2_SIM_URL": "http://127.0.0.1:8399",
    # An online lab gets course material the way a laptop does: it pulls
    # the class pack, and the strip offers anything new.
    "GO2_COURSE_WORK": "/home/student/work",
    "GO2_COURSE_URL": f"{CLASSROOM_URL}/course",
    # The student is already known here -- no laptop sign-in in the strip.
    "GO2_CLASS_IDENTITY": "off",
}
# Since JupyterHub 4, visiting a stopped lab's URL shows a "Launch" button
# instead of starting it. A student following a bookmark after the culler
# stopped their lab should not have to find a button: start it after a beat.
c.JupyterHub.implicit_spawn_seconds = 1
c.Spawner.start_timeout = 180
c.Spawner.http_timeout = 180                    # simulator warm-up, then Jupyter


def ensure_work_dir(spawner):
    """The student's folder, owned by the uid the lab image runs as.

    Created here rather than left to Docker, which would make it root's, and
    a student who cannot write to their own folder has no class.
    """
    path = os.path.join(WORK_ROOT, spawner.user.name)
    os.makedirs(path, exist_ok=True)
    os.chown(path, 1000, 1000)


c.Spawner.pre_spawn_hook = ensure_work_dir

# ------------------------------------------------------------ only while used
c.JupyterHub.services = [{
    "name": "idle-culler",
    "command": [sys.executable, "-m", "jupyterhub_idle_culler", "--timeout=3600"],
}]
c.JupyterHub.load_roles = [{
    "name": "idle-culler",
    "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],
    "services": ["idle-culler"],
}]

# ------------------------------------------------------------ the console
# The classroom console shows each student's online lab and can stop one.
# Its key can do exactly that: list users, read their labs' state and last
# activity, and stop a lab. It cannot start one, open one, or act as anyone.
if os.environ.get("GO2_HUB_API_TOKEN"):
    c.JupyterHub.services.append({
        "name": "classroom",
        "api_token": os.environ["GO2_HUB_API_TOKEN"],
    })
    c.JupyterHub.load_roles.append({
        "name": "classroom",
        "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],
        "services": ["classroom"],
    })
