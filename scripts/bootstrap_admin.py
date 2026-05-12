"""Create the initial ``admin`` user if missing.

Default credentials on first install are ``admin`` / ``admin`` (the system
forces a password change at the first successful login).

Re-running this command on an existing install is a no-op.
"""

from __future__ import annotations

import sys

from sqlalchemy.orm import Session

from middleware_monitor.core.db import init_engine, session_factory
from middleware_monitor.domain.auth.service import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    bootstrap_admin,
)


def main() -> int:
    init_engine()
    with session_factory() as db:  # type: Session
        user, plaintext = bootstrap_admin(db)
        bar = "=" * 60
        if plaintext:
            print(bar)
            print(" Middleware USCall Monitor — admin criado")
            print(bar)
            print(f" Usuário:  {user.username}")
            print(f" Senha:    {plaintext}")
            print(" (Você é obrigado a trocar a senha no primeiro login.")
            print("  Mínimo 12 caracteres com letras e números.)")
            print(bar)
        else:
            print(bar)
            print(f" Usuário '{DEFAULT_ADMIN_USERNAME}' já existe — nada a fazer.")
            print(" Se você esqueceu a senha, veja docs/RUNBOOK.md §7.")
            print(bar)
        return 0


if __name__ == "__main__":
    sys.exit(main())
