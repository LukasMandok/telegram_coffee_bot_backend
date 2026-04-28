"""Initial (first-start) setup for normal deployments.

This module runs safe, idempotent initialization on application startup:
- create default config/password/app settings if missing
- create passive users from the `users` environment variable (or fallback list)

This is intended for normal deployment startup and is deliberately
separated from `temp_debug_setup.py` which remains a development-only helper.
"""
import os
import asyncio
from typing import List, Tuple, Optional

from .common.log import Logger
from .dependencies.dependencies import get_repo
from .handlers import users as handlers
from .models import beanie_models as models

logger = Logger("InitialSetup")


def parse_passive_users_env() -> List[Tuple[str, str]]:
    """
    Parse passive users from environment variable `users` or `USERS`.

    Supported formats:
      - users=[First Last, First Last, SingleName, ...]
      - users=First Last,First2 Last2

    Parsing rules: first word -> first name, last word -> last name.
    If only one word is provided, last name will be an empty string.
    Middle names (when more than two words) are ignored.
    """
    raw = os.getenv("users") or os.getenv("USERS") or ""
    raw = raw.strip()
    if not raw:
        return []

    # Strip surrounding quotes
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()

    # Remove enclosing brackets if present
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].strip()

    if not raw:
        return []

    entries = [p.strip() for p in raw.split(",") if p.strip()]
    parsed: List[Tuple[str, str]] = []
    for entry in entries:
        parts = entry.split()
        if not parts:
            continue
        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""
        parsed.append((first, last))

    return parsed


async def run_initial_setup() -> None:
    """
    Run idempotent first-start initialization for normal deployments.

    This will create default Config/Password/AppSettings if they're missing
    and create passive users from the `users` env var (or fallback list).
    """
    logger.info("Running initial setup (defaults + passive users if missing)")

    repo = get_repo()

    # Create defaults only on fresh DB
    have_config = await models.Config.find_one() is not None
    have_password = await models.Password.find_one() is not None
    have_app_settings = await models.AppSettings.find_one() is not None

    if not (have_config and have_password and have_app_settings):
        logger.info("Defaults missing, running setup_defaults()")
        await repo.setup_defaults()  # type: ignore - BeanieRepository runtime
    else:
        logger.info("Defaults already exist; skipping setup_defaults()")

    # Build list of passive users from env
    users_list = parse_passive_users_env()

    created_count = 0
    existing_count = 0
    failed_count = 0

    for first_name, last_name in users_list:
        try:
            last_name_to_use: Optional[str] = last_name if last_name.strip() else None

            existing_user = await handlers.find_passive_user_by_name(
                first_name=first_name,
                last_name=last_name_to_use,
            )

            if existing_user:
                logger.debug(f"User already exists: {existing_user.display_name}")
                existing_count += 1
                continue

            new_user = await handlers.create_passive_user(
                first_name=first_name,
                last_name=last_name_to_use,
            )
            logger.info(f"Created passive user: {new_user.display_name}")
            created_count += 1

        except ValueError as e:
            logger.debug(f"Skipped {first_name} {last_name}: {e}")
            existing_count += 1
        except Exception as e:
            logger.error(f"Failed creating passive user {first_name} {last_name}", extra_tag="DB", exc=e)
            failed_count += 1

    logger.info(f"Initial setup complete: {created_count} created, {existing_count} existing, {failed_count} failed")


if __name__ == "__main__":
    asyncio.run(run_initial_setup())
