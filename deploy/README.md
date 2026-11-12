# Hosted mode — one JupyterLab per student

The student installs nothing. They open a link and get an editor, a terminal
and the dog rendering inline in notebook cells.

```
student's browser  ──►  bumblebee  ──►  simulated dog
   (a link)              container        in the notebook
```

## Running a class

```bash
cd deploy
./go2-student.sh create alice     # allocate port, token, work dir
./go2-student.sh up     alice     # build (first time) and start
./go2-student.sh link   alice     # the link to send them
./go2-student.sh list             # everyone's port, status, link, GPU use
./go2-student.sh down   alice     # stop
```

`create` copies the starter notebooks into the student's work directory. Ports
are allocated from **8400**, one per student, in creation order.

The link is `http://<host>:<port>/lab?token=<token>` — the token *is* the
login, so treat the link as a password and send it to that student only.

## Where work lives

`student-work/<name>/` on the host, bind-mounted to `/home/student/work` in
the container. It survives rebuilds, restarts and image changes, so you can
update the image mid-course without losing anybody's code.

## Resources

Measured on a Ryzen 9 9950X + RTX 5090:

| Per student | Cost |
|---|---|
| CPU | ~3.2% of one core (simulation) |
| RAM | ~60 MB marginal |
| VRAM | ~150 MB (rendering context) |

A class of 30 is roughly **1 core, 2 GB RAM and 4.5 GB VRAM**. The simulator
is not the constraint; it comfortably shares the box with Isaac Sim.

## Rendering

Frames render offscreen via **EGL on the host GPU** (~0.3 ms at 640×480). The
container asks the NVIDIA toolkit for `graphics` capability, not just
`compute` — without `graphics` there is no EGL and rendering fails.

To run with no GPU at all, set `MUJOCO_GL=osmesa` in the student's env file
and drop the `deploy.resources` block from `docker-compose.yml`. Software
rendering is slower but needs no driver.

## The real robot is off, deliberately

`GO2_ALLOW_REAL=0` is set in both the Dockerfile and the compose file, and
`Go2(sim=False)` raises `PermissionError` without it.

This is not a formality. A hosted notebook can be opened from anywhere, and
the real backend drives a 15 kg robot that may have nobody standing next to
it. **Never set `GO2_ALLOW_REAL=1` on a shared teaching server.** Hardware
work belongs on a machine where a human is present with the remote in hand,
during a booked slot.

There is a test asserting the interlock holds (`test_real_mode_is_disabled_by_default`).

## Security

JupyterLab gives each student a shell inside their own container as an
unprivileged user, with their own work directory. Containers are isolated from
each other, but note:

- The token is the only authentication. Anyone with the link has that
  student's environment. Rotate by editing `LAB_TOKEN` and restarting.
- Ports bind all interfaces. Reach them over the tailnet, or restrict with
  ufw, exactly as for the Isaac Sim viewer.
- Students can `pip install` inside their own container. That is intentional,
  and it cannot affect anyone else.

## Updating the course code mid-term

```bash
cd deploy
for f in students/*.env; do
  n=$(basename "$f" .env)
  docker compose --env-file "$f" -p "go2-$n" up -d --build
done
```

Student work is bind-mounted, so it is untouched. Only the installed `go2`
package changes.
