#!/usr/bin/env python
"""Compare the model catalogue against the provider's real /models endpoint.

Why this exists
---------------
Model ids go stale every few months, and third-party listings are wrong often
enough to matter: on 2026-11-17 aggregators listed ``deepseek-v4.1-flash``,
while the official endpoint returned only ``deepseek-flash`` and
``deepseek-v4-pro``. A catalogue entry that no longer exists fails at the first
analysis, not at "API Key 验证".

This probe reuses the production client and the production capability rules
(``model_catalog.resolve_capability``), so what it measures is what users get.

Usage
-----
    cd backend
    uv run python scripts/probe_llm_endpoints.py            # list + diff only
    uv run python scripts/probe_llm_endpoints.py --probe    # + one tiny call per id
    uv run python scripts/probe_llm_endpoints.py --probe --models kimi-k3.1-pro

The API key is read from the same secret store the app uses and is never
printed. ``--probe`` costs a few tokens per model.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.infra import config
from src.infra.model_catalog import PROVIDERS, resolve_capability
from src.infra.secret_store import load_api_key


def _saved_cloud_config() -> dict[str, str]:
    """Read provider/base_url/model from app_settings without touching secrets."""
    db = Path(config.DB_PATH)
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        keys = ("cloud_provider", "cloud_base_url", "cloud_model")
        rows = con.execute(
            f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?' * len(keys))})",
            keys,
        ).fetchall()
    finally:
        con.close()
    return {k.replace("cloud_", ""): v for k, v in rows}


def _preset_for(provider_id: str, base_url: str) -> dict | None:
    for preset in PROVIDERS:
        if preset["id"] == provider_id:
            return preset
    if base_url:
        for preset in PROVIDERS:
            if preset["base_url"] and preset["base_url"].rstrip("/") == base_url.rstrip("/"):
                return preset
    return None


async def _list_models(base_url: str, key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}
        )
        resp.raise_for_status()
        data = resp.json()
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


async def _probe_one(base_url: str, key: str, model: str) -> tuple[bool, str]:
    """One real completion, shaped exactly like production requests."""
    from src.infra.openai_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url, key, model)
    cap = resolve_capability(model)
    try:
        await client.generate("You are a health check.", "ping", max_tokens=8, timeout=60)
        return True, f"ok ({cap.max_tokens_field}, sampling={'on' if cap.sampling_params else 'off'})"
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        return False, f"{type(exc).__name__} status={status}: {str(exc)[:160]}"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true", help="send one tiny completion per model")
    ap.add_argument("--models", default="", help="comma-separated subset to probe")
    args = ap.parse_args()

    saved = _saved_cloud_config()
    provider_id = saved.get("provider", "")
    base_url = saved.get("base_url", "")
    saved_model = saved.get("model", "")
    preset = _preset_for(provider_id, base_url)

    print(f"data dir : {config.DATA_DIR}")
    print(f"provider : {provider_id or '(unset)'}")
    print(f"base_url : {base_url or '(unset)'}")
    print(f"model    : {saved_model or '(unset)'}")

    if not base_url:
        print("\nNo cloud endpoint configured; nothing to probe.")
        return 1

    key = await load_api_key()
    if not key:
        print("\nNo API key in the secret store; cannot probe.")
        return 1

    try:
        live = await _list_models(base_url, key)
    except Exception as exc:
        print(f"\nGET /models failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"\nendpoint lists {len(live)} models: {', '.join(live) or '(empty)'}")

    if saved_model and saved_model not in live:
        print(f"!! the saved model {saved_model!r} is NOT in the live list")

    catalogue = (preset or {}).get("models", [])
    if catalogue:
        missing = [m for m in catalogue if m not in live]
        print(f"\ncatalogue ({preset['id']}, preset {preset['default_model']}):")
        for model in catalogue:
            mark = "ok " if model in live else "GONE"
            print(f"  [{mark}] {model}")
        if missing:
            print(f"\n!! {len(missing)} catalogue id(s) not offered by the endpoint: {missing}")
        default = (preset or {}).get("default_model", "")
        if default and default not in live:
            print(f"!! default_model {default!r} is NOT in the live list")

    if args.probe:
        targets = [m.strip() for m in args.models.split(",") if m.strip()] or [
            m for m in catalogue if m in live
        ]
        print(f"\nprobing {len(targets)} model(s):")
        failures = 0
        for model in targets:
            ok, detail = await _probe_one(base_url, key, model)
            failures += 0 if ok else 1
            print(f"  [{'ok  ' if ok else 'FAIL'}] {model}: {detail}")
        if failures:
            print(f"\n{failures} probe(s) failed")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
