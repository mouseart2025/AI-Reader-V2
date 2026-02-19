"""Secure API key storage — keyring with SQLite fallback."""

import base64
import logging

logger = logging.getLogger(__name__)

_SERVICE_NAME = "ai-reader-v2"
_KEY_NAME = "llm-api-key"


def _try_keyring_save(api_key: str) -> bool:
    """Try saving via keyring. Returns True on success."""
    try:
        import keyring

        keyring.set_password(_SERVICE_NAME, _KEY_NAME, api_key)
        # Verify it was actually saved
        stored = keyring.get_password(_SERVICE_NAME, _KEY_NAME)
        return stored == api_key
    except Exception as e:
        logger.debug("keyring save failed: %s", e)
        return False


def _try_keyring_load() -> str | None:
    """Try loading from keyring. Returns None if unavailable."""
    try:
        import keyring

        return keyring.get_password(_SERVICE_NAME, _KEY_NAME)
    except Exception:
        return None


def _try_keyring_delete() -> bool:
    try:
        import keyring

        keyring.delete_password(_SERVICE_NAME, _KEY_NAME)
        return True
    except Exception:
        return False


async def _fallback_save(api_key: str) -> None:
    """Save API key to app_settings table (base64 obfuscated)."""
    from src.db.sqlite_db import get_connection

    encoded = base64.b64encode(api_key.encode()).decode()
    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('llm_api_key_b64', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (encoded,),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _fallback_load() -> str | None:
    """Load API key from app_settings table."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        row = await conn.execute(
            "SELECT value FROM app_settings WHERE key='llm_api_key_b64'",
        )
        result = await row.fetchone()
        if result and result[0]:
            try:
                return base64.b64decode(result[0].encode()).decode()
            except Exception:
                return None
    finally:
        await conn.close()
    return None


async def _fallback_delete() -> None:
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM app_settings WHERE key='llm_api_key_b64'",
        )
        await conn.commit()
    finally:
        await conn.close()


async def save_api_key(api_key: str) -> str:
    """Save API key. Returns storage method used: 'keyring' or 'database'."""
    if _try_keyring_save(api_key):
        # Clear any fallback entry
        await _fallback_delete()
        return "keyring"
    # Fallback to database
    await _fallback_save(api_key)
    return "database"


async def load_api_key() -> str | None:
    """Load API key from keyring or fallback."""
    key = _try_keyring_load()
    if key:
        return key
    return await _fallback_load()


async def delete_api_key() -> None:
    """Delete API key from all stores."""
    _try_keyring_delete()
    await _fallback_delete()
