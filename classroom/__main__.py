"""Run the classroom site.

    python -m classroom                      # 127.0.0.1:8500
    python -m classroom --host 0.0.0.0       # behind your own nginx

Behind a reverse proxy, uvicorn is told to trust the forwarding headers so
that it knows the request arrived over https -- which is what makes the
session cookie Secure and the Google redirect URI correct.
"""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="classroom",
                                 description="The Robo Dog classroom site.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default: 127.0.0.1, i.e. only a "
                         "reverse proxy on this machine can reach it)")
    ap.add_argument("--port", type=int, default=8500)
    ap.add_argument("--reload", action="store_true", help="for development")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    print(f"classroom on http://{args.host}:{args.port}")
    print(f"  public name   {settings.base_url}")
    print(f"  install line  curl -fsSL {settings.install_url} | bash")
    print(f"  mentors       {', '.join(sorted(settings.teachers)) or '(none set -- set GO2_TEACHERS)'}")
    print(f"  google        {'configured' if settings.google_configured else 'NOT configured (join codes still work)'}")
    print(f"  database      {settings.db_path}")
    print(f"  trusts        {settings.trusted_proxy}  (X-Forwarded-* from here only)")

    uvicorn.run(create_app(settings) if not args.reload else "classroom.app:app",
                host=args.host, port=args.port, reload=args.reload,
                proxy_headers=True,
                forwarded_allow_ips=settings.trusted_proxy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
