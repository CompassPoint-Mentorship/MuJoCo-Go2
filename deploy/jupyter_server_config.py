# Settings for every lab started from this image, by JupyterHub or not.
#
# Framing: the split page shows JupyterLab in one frame and the dog in the
# other, both from the same site. Jupyter's own default allows that
# ("frame-ancestors 'self'"), but jupyterhub-singleuser replaces it with
# 'none' -- never framed, by anyone -- which left the online labs' split view
# with an empty left half. It does so with setdefault, so setting it here wins.
#
# 'self' keeps what 'none' is for: no other website can frame a student's lab.
# And on this shared origin, another student's lab cannot do it either,
# because JupyterHub lets only a lab's owner load any page from it -- so no
# other student's page can run in your browser on this site in the first
# place.
c = get_config()  # noqa: F821
c.ServerApp.tornado_settings = {
    "headers": {"Content-Security-Policy": "frame-ancestors 'self'"},
}
