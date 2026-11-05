"""Command line for a human in a terminal: save or remove a store login.

    handle-mcp login nemlig             asks for email and password, checks them, saves in the keychain
    handle-mcp login nemlig --from-env  reads HANDLE_NEMLIG_EMAIL/_PASSWORD, checks them, saves
                                        in ~/.config/handle-mcp/nemlig.json (chmod 600). For a
                                        headless machine or an AI agent with no terminal.
    handle-mcp logout nemlig            removes the login from the keychain and the file

The password is never echoed or logged. Only `--from-env` writes it to disk, owner-readable only.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from keyring.errors import KeyringError

from handle_mcp import stores as registry


def _say(text: str) -> None:
    sys.stderr.write(text + "\n")


def run(args: list[str]) -> int:
    known = ", ".join(sorted(registry.STORES))
    from_env = "--from-env" in args
    args = [a for a in args if a != "--from-env"]
    if len(args) != 2 or args[0] not in ("login", "logout") or args[1] not in registry.STORES:
        _say(
            "Brug: handle-mcp login <butik> [--from-env]  eller  handle-mcp logout <butik>\n"
            f"Butikker: {known}"
        )
        return 2
    command, store = args
    if command == "login" and not getattr(registry.STORES[store], "needs_login", True):
        _say(f"{store} kræver intet login.")
        return 0
    if command == "logout":
        return _logout(store)
    if from_env:
        return _login_from_env(store)

    _say(f"Log ind på {store}. Brug den email og det kodeord, du bruger på butikkens hjemmeside.")
    sys.stderr.write(f"Email til {store}: ")
    sys.stderr.flush()
    email = sys.stdin.readline().strip()
    password = getpass.getpass(f"Kodeord til {store} (vises ikke): ")
    if not email or not password:
        _say("Email og kodeord skal udfyldes. Intet er gemt.")
        return 1
    try:  # check first, save after: a failed check never touches a saved login
        _check(store, email, password)
    except registry.StoreError as exc:
        _say(f"Login blev ikke gemt: {exc}\nEt tidligere gemt login er uændret.")
        return 1
    try:
        registry.keychain_set(store, email, password)
    except KeyringError as exc:
        _say(f"Kunne ikke gemme i nøgleringen: {exc}")
        return 1
    _say(f"Logget ind på {store}. Login er gemt i nøgleringen.")
    return 0


def _logout(store: str) -> int:
    removed = ["config-filen"] if registry.config_delete(store) else []
    try:
        if registry.keychain_delete(store):
            removed.append("nøgleringen")
    except KeyringError as exc:
        if not removed:
            _say(f"Ingen nøglering på denne maskine: {exc}")
            return 1
    _say(f"Login slettet fra {' og '.join(removed)}." if removed else "Der var intet gemt login.")
    return 0


def _login_from_env(store: str) -> int:
    """No terminal, no keychain: a headless box or an AI agent. The env vars are read once,
    checked against the store and saved in a file only the owner can read."""
    prefix = f"HANDLE_{store.upper()}_"
    email, password = os.environ.get(prefix + "EMAIL"), os.environ.get(prefix + "PASSWORD")
    if not email or not password:
        _say(f"Sæt {prefix}EMAIL og {prefix}PASSWORD, og kør igen. Intet er gemt.")
        return 1
    try:  # check first, save after: a failed check never touches a saved login
        _check(store, email, password)
    except registry.StoreError as exc:
        _say(f"Login blev ikke gemt: {exc}\nEt tidligere gemt login er uændret.")
        return 1
    path = registry.config_set(store, email, password)
    _say(f"Logget ind på {store}. Login er gemt i {path} (kun du kan læse den).")
    try:
        shadowed = registry.keychain_get(store) is not None
    except registry.StoreError:  # a broken entry: load_login will complain about it too
        shadowed = True
    if shadowed:  # load_login reads the keychain first
        _say(
            f"OBS: nøgleringen har også et login til {store}, og det vinder over filen. "
            f"Fjern det med: handle-mcp logout {store}, og kør så --from-env igen."
        )
    return 0


def _check(store: str, email: str, password: str) -> None:
    """A real, read-only login with these values. They win over saved ones via env vars."""
    prefix = f"HANDLE_{store.upper()}_"
    keys = {prefix + "EMAIL": email, prefix + "PASSWORD": password}
    before = {k: os.environ.get(k) for k in keys}
    os.environ.update(keys)
    try:
        asyncio.run(registry.STORES[store]().get_cart())
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
