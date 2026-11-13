"""The Robo Dog classroom site: promo page, student page, mentor console.

Separate from the `go2` package on purpose. The simulator is the thing
students install on their own laptops and must keep working with no network;
this is a service that runs on one machine, owns a database and shells out to
docker. Mixing them would put a web framework in the dependency list of every
student install.
"""

__all__ = ["create_app"]


def create_app(settings=None):
    from .app import create_app as _create_app

    return _create_app(settings)
