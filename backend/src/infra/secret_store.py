"""Secure API key storage — keyring with SQLite fallback."""

import asyncio
import base64
import logging
import os

logger = logging.getLogger(__name__)

_SERVICE_NAME = "ai-reader-v2"
_KEY_NAME = "llm-api-key"

# macOS 上 ad-hoc 签名的 sidecar 访问他方创建的 keychain 条目时，Security
# 框架会等待一个后台进程永远无法弹出的授权弹窗，get_password 永久阻塞
# （启动挂死的根因）。keyring 调用统一走线程池并加上限，超时回退数据库。
_KEYRING_TIMEOUT_S = 5.0

# 一次超时后整个进程不再尝试 keyring：挂起原因（签名不匹配的授权弹窗）
# 在进程生命周期内不会自愈，重复尝试只会反复付出 5s 停顿。
_keyring_suspect = False


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


async def _keyring_call(func, *args):
    """Run a blocking keyring call in a thread with a hard timeout.

    Returns None on timeout/error. A timed-out worker thread may stay stuck
    inside the Security framework; it is a daemon-level leak, not a deadlock —
    startup and shutdown proceed regardless.
    """
    global _keyring_suspect
    if _keyring_suspect:
        return None
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func, *args), timeout=_KEYRING_TIMEOUT_S
        )
    except TimeoutError:
        _keyring_suspect = True
        logger.warning(
            "keyring call timed out (%.0fs); keyring disabled for this process, "
            "falling back to database", _KEYRING_TIMEOUT_S,
        )
        return None
    except Exception as e:
        logger.warning("keyring call failed (%s); falling back to database", e)
        return None


async def save_api_key(api_key: str) -> str:
    """Save API key. Returns storage method used: 'keyring' or 'database'."""
    if await _keyring_call(_try_keyring_save, api_key):
        # Clear any fallback entry
        await _fallback_delete()
        return "keyring"
    # Fallback to database
    await _fallback_save(api_key)
    return "database"


async def load_api_key() -> str | None:
    """Load API key from keyring or fallback.

    Set ``AI_READER_FORCE_DB_KEY=1`` to read the key from the app_settings
    table instead of the OS keyring. The keyring is machine-global (shared by
    every data dir), so isolating a scratch/alternate data dir on a different
    provider requires bypassing it — deleting the keyring entry is unreliable
    on macOS (keychain raises error 100001). Default behaviour unchanged.
    """
    if os.environ.get("AI_READER_FORCE_DB_KEY"):
        return await _fallback_load()
    key = await _keyring_call(_try_keyring_load)
    if key:
        return key
    return await _fallback_load()


async def delete_api_key() -> None:
    """Delete API key from all stores."""
    await _keyring_call(_try_keyring_delete)
    await _fallback_delete()
