"""The stores. A new store = one file here + one line in STORES.

Every store is a class with the async methods in `Store`. If a store cannot do
something, the method raises NotImplementedError and the tool says so politely.
Store methods may only *read*, except `set_quantities`. Ordering, paying,
choosing a delivery time and changing the address must never exist here.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from handle_mcp.models import (
    Cart,
    DeliveryInfo,
    DeliverySlot,
    Order,
    Product,
    QuantityResult,
    Recipe,
    RecipeDetails,
    StoreTerms,
)

CONFIG_DIR = Path(os.environ.get("HANDLE_CONFIG_DIR", "~/.config/handle-mcp")).expanduser()
KEYCHAIN_SERVICE = "handle-mcp"  # one entry per store; the secret is {"email", "password"} as JSON


class StoreError(Exception):
    """A clear, user-facing failure from one store (login failed, API changed, ...)."""


class Store(Protocol):
    name: str

    async def search(self, query: str, limit: int = 10) -> list[Product]: ...
    async def recipes(self, query: str, limit: int = 5) -> list[Recipe]: ...
    async def recipe(self, recipe_id: str, persons: int | None = None) -> RecipeDetails: ...
    async def featured_recipes(self, limit: int = 20) -> list[Recipe]: ...
    async def recipes_by_tag(
        self, tags: list[str], category: str | None = None, limit: int = 20
    ) -> list[Recipe]: ...
    async def offers(self, limit: int = 25) -> list[Product]: ...
    async def get_cart(self) -> Cart: ...
    async def set_quantities(self, items: dict[str, int]) -> list[QuantityResult]: ...
    async def delivery_info(self) -> DeliveryInfo: ...
    async def delivery_slots(self, days: int = 7) -> list[DeliverySlot]: ...
    async def terms(self) -> StoreTerms: ...
    async def orders(self, limit: int = 10) -> list[Order]: ...
    async def order(self, order_id: str) -> Order: ...


def keychain_get(store: str) -> dict[str, str] | None:
    """The login saved by `handle-mcp login <store>` in the OS keychain, if any."""
    try:
        secret = keyring.get_password(KEYCHAIN_SERVICE, store)
    except KeyringError:  # no keychain on this machine (e.g. a headless Linux box)
        return None
    if not secret:
        return None
    try:
        data = json.loads(secret)
        return {"email": data["email"], "password": data["password"]}
    except (ValueError, KeyError, TypeError):
        raise StoreError(f"Ugyldigt login i nøgleringen. Kør: handle-mcp login {store}") from None


def keychain_set(store: str, email: str, password: str) -> None:
    keyring.set_password(
        KEYCHAIN_SERVICE, store, json.dumps({"email": email, "password": password})
    )


def keychain_delete(store: str) -> bool:
    """True if a login was removed, False if none was saved. Raises KeyringError with no keychain."""
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, store)
    except PasswordDeleteError:
        return False
    return True


def config_set(store: str, email: str, password: str) -> Path:
    """Save a login in ~/.config/handle-mcp/<store>.json, readable by the owner only.
    Used by `handle-mcp login <store> --from-env` where there is no keychain."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(stat.S_IRWXU)
    path = CONFIG_DIR / f"{store}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, stat.S_IRUSR | stat.S_IWUSR)
    if hasattr(os, "fchmod"):  # an existing wider file: tighten before writing (not on Windows)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    os.ftruncate(fd, 0)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"email": email, "password": password}, f)
    return path


def config_delete(store: str) -> bool:
    """True if ~/.config/handle-mcp/<store>.json was removed."""
    try:
        (CONFIG_DIR / f"{store}.json").unlink()
    except FileNotFoundError:
        return False
    return True


def has_login(store: str) -> bool:
    """True when load_login(store) would succeed. For calls that work without one."""
    try:
        load_login(store)
    except StoreError:
        return False
    return True


def load_login(store: str) -> dict[str, str]:
    """A store's login. First found wins:

    1. Env vars HANDLE_<STORE>_EMAIL and HANDLE_<STORE>_PASSWORD.
    2. The OS keychain (macOS Keychain, Windows Credential Locker, Secret Service),
       saved with `handle-mcp login <store>`. Recommended: encrypted at rest.
    3. ~/.config/handle-mcp/<store>.json, readable by its owner only (chmod 600).
    """
    prefix = f"HANDLE_{store.upper()}_"
    email, password = os.environ.get(prefix + "EMAIL"), os.environ.get(prefix + "PASSWORD")
    if email and password:
        return {"email": email, "password": password}
    if saved := keychain_get(store):
        return saved

    path = CONFIG_DIR / f"{store}.json"
    if not path.exists():
        raise StoreError(
            f"Intet login til {store}. Kør i en terminal: handle-mcp login {store}. "
            f"Uden terminal: sæt HANDLE_{store.upper()}_EMAIL og _PASSWORD, "
            f"og kør: handle-mcp login {store} --from-env"
        )
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise StoreError(f"{path} kan læses af andre. Kør: chmod 600 {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("email") or not data.get("password"):
        raise StoreError(f"{path} mangler 'email' eller 'password'.")
    return {"email": data["email"], "password": data["password"]}


from handle_mcp.stores.aarstiderne import AarstiderneStore
from handle_mcp.stores.bilkatogo import BilkaStore
from handle_mcp.stores.nemlig import NemligStore
from handle_mcp.stores.rema import RemaStore

STORES: dict[str, type[Store]] = {
    "nemlig": NemligStore,
    "bilkatogo": BilkaStore,
    "rema": RemaStore,
    "aarstiderne": AarstiderneStore,
}
