# The classroom site, as a container.
#
# It is NOT an isolation boundary, and pretending otherwise would be the
# dangerous mistake here: this app's whole job includes starting and stopping
# student labs, which means it holds the host's docker socket, which is
# equivalent to root on the host. Running it on the host as a user in the
# docker group has exactly the same power. The container buys restart policy,
# a fixed Python environment and the same operational shape as everything
# else here -- not safety from itself.
#
# Three things make it work at all, and each would be a puzzling bug if
# missed; see docker-compose.yml, where they are set:
#
#   * the repository is mounted at the SAME absolute path it has on the host,
#     because go2-student.sh writes host paths into env files that the host's
#     docker daemon later resolves (a bind mount of /srv/... from inside a
#     container is still interpreted by the daemon, on the host);
#   * host networking, so the lab links this app prints carry the host's own
#     address rather than a container's;
#   * the docker CLI and the compose plugin, installed below, because the
#     script it drives speaks `docker compose`.

FROM python:3.12-slim

# docker CLI + compose plugin from Docker's own repository: the Debian
# packages lag, and compose v1 (docker-compose) is not what the script calls.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
         | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
         > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Only the web dependencies. The simulator is not imported here -- this app
# never touches mujoco, and installing it would add 250 MB for nothing.
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.27" "jinja2>=3.1" \
        "python-multipart>=0.0.9" "itsdangerous>=2.1" "authlib>=1.3" \
        "httpx>=0.27"

# No application code is copied in: the repository is mounted, so the site,
# the course material and install/mac.sh are always the checkout on disk.
# That is the point of the mirror -- what this serves is what this machine
# has, not what an image was built from three weeks ago.
WORKDIR /srv/MuJoCo-Go2
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8500
CMD ["python", "-m", "classroom", "--host", "0.0.0.0", "--port", "8500"]
