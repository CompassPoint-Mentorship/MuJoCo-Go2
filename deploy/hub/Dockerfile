# JupyterHub for the online labs. The official image carries the matching
# Node and configurable-http-proxy; this adds the spawner, the culler and our
# sign-in.
FROM quay.io/jupyterhub/jupyterhub:6.2.0

RUN pip install --no-cache-dir "dockerspawner==14.0.0" "jupyterhub-idle-culler==2.0.0"

COPY jupyterhub_config.py go2_handoff.py handoff_token.py /srv/go2-hub/
WORKDIR /srv/jupyterhub
CMD ["jupyterhub", "-f", "/srv/go2-hub/jupyterhub_config.py"]
