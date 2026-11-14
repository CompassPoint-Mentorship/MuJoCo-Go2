# Documentation

Everything about the platform, in one place. Component READMEs live beside
their code and are listed here.

## Start here

| Document | For |
|---|---|
| [PLATFORM.md](PLATFORM.md) | **what has been built**: the pieces, how they fit, decisions and why, what was verified live, what is open |
| [OPERATIONS.md](OPERATIONS.md) | **running it**: containers, `.env`, updating, health, rotating secrets, fixing things, packing |
| [DESIGN.md](DESIGN.md) | the simulator: the one API for a simulated and a real dog |
| [STEP2-REAL-ROBOT.md](STEP2-REAL-ROBOT.md) | the plan for driving the real Go2 |

## Beside the code

| Document | About |
|---|---|
| [../README.md](../README.md) | the simulator package, the laptop installer, the API |
| [../classroom/README.md](../classroom/README.md) | the website: setup, Google sign-in, email, replies, the console |
| [../deploy/README.md](../deploy/README.md) | the lab image and the old per-port labs |
| [../deploy/hub/README.md](../deploy/hub/README.md) | the online labs: JupyterHub, the trial, switching on |
| [../.env.example](../.env.example) | every setting, explained |

## For other people and agents

| Document | For |
|---|---|
| [gateway/NGINX-REQUIREMENTS.md](gateway/NGINX-REQUIREMENTS.md) | whoever runs the nginx gateway: the whole contract |
| [gateway/GATEWAY-CHANGE-labs.md](gateway/GATEWAY-CHANGE-labs.md) | the `/labs/` change for online labs, with checks and undo |
| [agents/CLAUDE.md](agents/CLAUDE.md) | coding agents: conventions, commands, rules learned the hard way |
| [agents/README.md](agents/README.md) | which brief goes to which agent; the agents' memory notes |

## Pictures

[screenshots/](screenshots/) — the student install, used by the README.

## Packing

`tools/pack.sh` makes `dist/robo-dog-<date>-<commit>.tar.gz` (code and all of
the above); `--with-state` adds a separate archive of secrets and student data.
See OPERATIONS.md.
