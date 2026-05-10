"""Create the initial ``admin`` user if missing.

Run this once after installation. Prints the temporary password to stdout
exactly once; if the user already exists, prints nothing and exits 0.
"""

from __future__ import annotations

import sys

from sqlalchemy.orm import Session

from middleware_monitor.core.db import init_engine, session_factory
from middleware_monitor.domain.auth.service import bootstrap_admin


def main() -> int:
    init_engine()
    with session_factory() as db:  # type: Session
        user, plaintext = bootstrap_admin(db)
        if plaintext:
            print("=" * 60)
            print(" Middleware USCall Monitor — admin created")
            print("=" * 60)
            print(f" username: {user.username}")
            print(f" password: {plaintext}")
            print(" (you will be required to change it on first login)")
            print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
