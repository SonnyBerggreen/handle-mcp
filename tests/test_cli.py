"""`handle-mcp login/logout <store>`: saves in the keychain only when the login works."""

import io
import os
import sys

import pytest

from handle_mcp import cli
from handle_mcp import stores as registry
from tests.conftest import FakeStore


class CheckingStore(FakeStore):
    async def get_cart(self):
        if registry.load_login("fake")["password"] != "rigtig":
            raise registry.StoreError("Tjek email og kodeord.")
        return await super().get_cart()


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(registry, "STORES", {"fake": CheckingStore})

    def type_in(email, password):
        monkeypatch.setattr("sys.stdin", io.StringIO(email + "\n"))
        monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: password)

    return type_in


def test_login_saves_a_working_login(terminal, memory_keychain, capsys):
    terminal("a@example.com", "rigtig")
    assert cli.run(["login", "fake"]) == 0
    assert registry.keychain_get("fake") == {"email": "a@example.com", "password": "rigtig"}
    out = capsys.readouterr()
    assert out.out == ""  # nothing on stdout, and the password is never shown
    assert "rigtig" not in out.err and "Logget ind" in out.err


def test_wrong_password_saves_nothing(terminal, memory_keychain, capsys):
    terminal("a@example.com", "forkert")
    assert cli.run(["login", "fake"]) == 1
    assert registry.keychain_get("fake") is None
    assert "Tjek email og kodeord" in capsys.readouterr().err


def test_logout_removes_the_login(memory_keychain):
    registry.keychain_set("fake", "a@example.com", "x")
    assert cli.run(["logout", "fake"]) == 0
    assert registry.keychain_get("fake") is None


def test_unknown_command_or_store_shows_usage(capsys):
    assert cli.run(["login", "netto"]) == 2
    assert "Brug: handle-mcp login" in capsys.readouterr().err


def test_failed_relogin_keeps_the_old_login(terminal, memory_keychain):
    registry.keychain_set("fake", "a@example.com", "rigtig")
    terminal("a@example.com", "forkert")
    assert cli.run(["login", "fake"]) == 1
    assert registry.keychain_get("fake") == {"email": "a@example.com", "password": "rigtig"}
    assert "HANDLE_FAKE_PASSWORD" not in os.environ  # the check leaves no env vars


def test_logout_without_a_keychain_says_so(monkeypatch, capsys):
    from keyring.errors import NoKeyringError

    def no_keychain(*_args):
        raise NoKeyringError("no backend")

    monkeypatch.setattr(registry.keyring, "delete_password", no_keychain)
    assert cli.run(["logout", "fake"]) == 1
    assert "Ingen nøglering" in capsys.readouterr().err


@pytest.mark.parametrize("secret", ["not json", '{"email": "a@example.com"}', "[]"])
def test_broken_keychain_entry_gives_a_clear_error(memory_keychain, secret):
    memory_keychain.secrets[(registry.KEYCHAIN_SERVICE, "fake")] = secret
    with pytest.raises(registry.StoreError, match="Ugyldigt login i nøgleringen"):
        registry.keychain_get("fake")


def test_load_login_surfaces_a_broken_keychain_entry(memory_keychain):
    memory_keychain.secrets[(registry.KEYCHAIN_SERVICE, "fake")] = "not json"
    with pytest.raises(registry.StoreError, match="handle-mcp login fake"):
        registry.load_login("fake")


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path / "handle-mcp")
    monkeypatch.setattr(registry, "STORES", {"fake": CheckingStore})
    return tmp_path / "handle-mcp"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes (chmod 600)")
def test_login_from_env_writes_an_owner_only_file(config_dir, monkeypatch, capsys):
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "rigtig")
    assert cli.run(["login", "fake", "--from-env"]) == 0
    path = config_dir / "fake.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert config_dir.stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv("HANDLE_FAKE_EMAIL")
    monkeypatch.delenv("HANDLE_FAKE_PASSWORD")
    assert registry.load_login("fake") == {"email": "a@example.com", "password": "rigtig"}
    out = capsys.readouterr()
    assert out.out == "" and "rigtig" not in out.err and str(path) in out.err


def test_login_from_env_with_a_wrong_password_saves_nothing(config_dir, monkeypatch, capsys):
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "forkert")
    assert cli.run(["login", "fake", "--from-env"]) == 1
    assert not (config_dir / "fake.json").exists()
    assert "Tjek email og kodeord" in capsys.readouterr().err


def test_login_from_env_without_env_vars_says_which(config_dir, monkeypatch, capsys):
    monkeypatch.delenv("HANDLE_FAKE_EMAIL", raising=False)
    monkeypatch.delenv("HANDLE_FAKE_PASSWORD", raising=False)
    assert cli.run(["login", "fake", "--from-env"]) == 1
    assert "HANDLE_FAKE_EMAIL" in capsys.readouterr().err


def test_logout_removes_the_file_too(config_dir, capsys):
    registry.config_set("fake", "a@example.com", "x")
    assert cli.run(["logout", "fake"]) == 0
    assert not (config_dir / "fake.json").exists()
    assert "slettet fra config-filen." in capsys.readouterr().err  # one line, not two


def test_logout_removes_file_and_keychain_in_one_line(config_dir, capsys):
    registry.config_set("fake", "a@example.com", "x")
    registry.keychain_set("fake", "a@example.com", "x")
    assert cli.run(["logout", "fake"]) == 0
    assert "slettet fra config-filen og nøgleringen." in capsys.readouterr().err


def test_logout_file_only_without_a_keychain(config_dir, monkeypatch, capsys):
    from keyring.errors import NoKeyringError

    def no_keychain(*_args):
        raise NoKeyringError("no backend")

    monkeypatch.setattr(registry.keyring, "delete_password", no_keychain)
    registry.config_set("fake", "a@example.com", "x")
    assert cli.run(["logout", "fake"]) == 0
    assert "slettet fra config-filen." in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes (chmod 600)")
def test_login_from_env_tightens_an_existing_wide_file(config_dir, monkeypatch):
    config_dir.mkdir()
    old = config_dir / "fake.json"
    old.write_text('{"email": "old@example.com", "password": "gammelt-og-langt-kodeord"}')
    old.chmod(0o644)
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "rigtig")
    assert cli.run(["login", "fake", "--from-env"]) == 0
    assert old.stat().st_mode & 0o777 == 0o600
    assert (
        old.read_text(encoding="utf-8") == '{"email": "a@example.com", "password": "rigtig"}'
    )  # no old tail


def test_login_from_env_warns_when_the_keychain_wins(config_dir, monkeypatch, capsys):
    registry.keychain_set("fake", "old@example.com", "rigtig")
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "rigtig")
    assert cli.run(["login", "fake", "--from-env"]) == 0
    assert "nøgleringen har også et login" in capsys.readouterr().err


def test_missing_login_error_names_the_headless_way(config_dir):
    with pytest.raises(registry.StoreError, match="handle-mcp login fake --from-env"):
        registry.load_login("fake")


def test_login_from_env_survives_a_broken_keychain_entry(
    config_dir, memory_keychain, monkeypatch, capsys
):
    memory_keychain.secrets[(registry.KEYCHAIN_SERVICE, "fake")] = "not json"
    monkeypatch.setenv("HANDLE_FAKE_EMAIL", "a@example.com")
    monkeypatch.setenv("HANDLE_FAKE_PASSWORD", "rigtig")
    assert cli.run(["login", "fake", "--from-env"]) == 0
    assert (config_dir / "fake.json").exists()
    assert "nøgleringen har også et login" in capsys.readouterr().err


def test_login_to_a_store_without_login_saves_nothing(monkeypatch, capsys):
    class OpenStore:
        needs_login = False

    saved = []
    monkeypatch.setattr(registry, "STORES", {"open": OpenStore})
    monkeypatch.setattr(registry, "keychain_set", lambda *a: saved.append(a))
    assert cli.run(["login", "open"]) == 0
    assert saved == [] and "kræver intet login" in capsys.readouterr().err
