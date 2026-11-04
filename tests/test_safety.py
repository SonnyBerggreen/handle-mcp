"""The server can never order, pay or change the address, and keeps secrets out."""

import os
import re
import stat
import sys
from pathlib import Path

import pytest

from handle_mcp import stores as registry

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"

# Endpoints and words that must never appear in the code.
FORBIDDEN = [
    r"PlaceOrder",
    r"Checkout/",
    r"GetCreditCards",
    r"GetCardsFees",
    r"/payment",
    r"SetDeliveryAddress",
    r"SetDeliveryTime",
    r"UpdateDeliveryTime",  # nemlig: also TryUpdateDeliveryTime; chooses a slot
    r"updateTimeslot",
    r"SetDeliveryDate",  # BilkaToGo: chooses a slot
    r"ReserveTimeslot",
    r"ChangeAddress",
    r"EmptyCart",
    r"ClearBasket",  # nemlig: empties the whole cart
    r"CancelOrder",
    r"AddRecipeToBasket",  # nemlig: fills the cart without the human's yes
]


def _source_files():
    return [p for p in SRC.rglob("*.py")]


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_forbidden_endpoint_not_in_code(pattern):
    hits = [
        str(p)
        for p in _source_files()
        if re.search(pattern, p.read_text(encoding="utf-8"), re.IGNORECASE)
    ]
    assert not hits, f"{pattern} found in {hits}"


def test_nothing_prints_to_stdout():
    """stdout is the JSON-RPC stream; a stray print() breaks the protocol."""
    hits = [
        str(p) for p in _source_files() if re.search(r"\bprint\(", p.read_text(encoding="utf-8"))
    ]
    assert not hits


def test_httpx_does_not_log_urls():
    """httpx logs every URL at INFO; nemlig's offer URL holds the personal UserId."""
    import logging

    from handle_mcp import server

    server.configure_logging()
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_gitignore_keeps_secrets_out():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "*.secret.json"):
        assert entry in ignored


def test_login_from_env(monkeypatch):
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "x")
    assert registry.load_login("fake") == {"email": "a@example.com", "password": "x"}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes (chmod 600)")
def test_login_file_must_be_private(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path)
    path = tmp_path / "fake.json"
    path.write_text('{"email": "a@example.com", "password": "x"}')
    os.chmod(path, 0o644)
    with pytest.raises(registry.StoreError, match="chmod 600"):
        registry.load_login("fake")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    assert registry.load_login("fake")["email"] == "a@example.com"


def test_missing_login_says_how_to_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path)
    with pytest.raises(registry.StoreError, match="handle-mcp login fake"):
        registry.load_login("fake")


def test_login_from_keychain_beats_file(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path)
    (tmp_path / "fake.json").write_text('{"email": "file@example.com", "password": "f"}')
    registry.keychain_set("fake", "kc@example.com", "k")
    assert registry.load_login("fake") == {"email": "kc@example.com", "password": "k"}
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "env@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "e")
    assert registry.load_login("fake")["email"] == "env@example.com"  # env wins


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes (chmod 600)")
def test_no_keychain_falls_back_to_file(tmp_path, monkeypatch):
    from keyring.errors import NoKeyringError

    def broken(*_):
        raise NoKeyringError("no backend")

    monkeypatch.setattr(registry.keyring, "get_password", broken)
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path)
    path = tmp_path / "fake.json"
    path.write_text('{"email": "a@example.com", "password": "x"}')
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    assert registry.load_login("fake")["email"] == "a@example.com"


def test_live_failures_do_not_print_logins(pytester, monkeypatch):
    """A failed live login must not show the password in pytest's traceback."""
    monkeypatch.setenv("HANDLE_LIVE", "1")
    monkeypatch.setenv("FAKE_PASSWORD", "hemmeligt-4711")
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function")
    pytester.makeconftest("from tests.conftest import pytest_configure  # noqa: F401")
    pytester.makepyfile(
        """
        import os

        def send(data):
            raise RuntimeError("blocked by the sandbox")

        def test_login():
            send(data={"password": os.environ["FAKE_PASSWORD"]})
        """
    )
    result = pytester.runpytest("-l")
    result.assert_outcomes(failed=1)
    assert "hemmeligt-4711" not in result.stdout.str()
