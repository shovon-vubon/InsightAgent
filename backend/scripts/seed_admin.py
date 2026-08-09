"""Create or promote the bootstrap admin account.

Run via `make seed`. Deliberately a script rather than an API endpoint: an
endpoint that mints administrators is a privilege-escalation surface that would
have to be defended forever.
"""

from __future__ import annotations

import asyncio
import os
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_database
from app.services.auth import AuthService

logger = get_logger("scripts.seed_admin")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)

    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        logger.error("seed_admin_missing_credentials")
        return 1
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        logger.error("seed_admin_password_too_short", minimum=settings.PASSWORD_MIN_LENGTH)
        return 1

    database = create_database(settings)
    try:
        async with database.session() as session:
            admin = await AuthService(session, settings).ensure_admin(
                email=email, password=password
            )
            logger.info("seed_admin_ready", user_id=str(admin.id), role=admin.role.value)
    finally:
        await database.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
