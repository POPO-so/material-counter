"""Registry boundary tests for browser discovery in the Windows launcher."""
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest


def load_web_launcher():
    path = Path(__file__).resolve().parents[2] / "Python脚本" / "启动网页.pyw"
    module = types.ModuleType("web_launcher")
    module.__file__ = str(path)
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


class FakeWinReg(types.ModuleType):
    HKEY_CURRENT_USER = object()
    HKEY_CLASSES_ROOT = object()

    def __init__(self, progid="BrowserHTML", command=""):
        super().__init__("winreg")
        self.progid = progid
        self.command = command
        self.missing = set()

    def OpenKey(self, root, key):
        if (root, key) in self.missing:
            raise FileNotFoundError(key)
        if root is self.HKEY_CURRENT_USER and key == (
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"):
            return nullcontext(("user-choice",))
        if root is self.HKEY_CLASSES_ROOT and key == self.progid + r"\shell\open\command":
            return nullcontext(("open-command",))
        raise FileNotFoundError(key)

    def QueryValueEx(self, key, value_name):
        if key == ("user-choice",) and value_name == "ProgId":
            if self.progid is None:
                raise FileNotFoundError("ProgId")
            return self.progid, 1
        if key == ("open-command",) and value_name == "":
            return self.command, 1
        raise FileNotFoundError(value_name)


def install_registry(monkeypatch, command, progid="BrowserHTML"):
    registry = FakeWinReg(progid=progid, command=command)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    return registry


def test_default_browser_executable_parses_quoted_path_with_spaces(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / "Browser App" / "browser.exe"
    browser.parent.mkdir()
    browser.touch()
    install_registry(monkeypatch, f'"{browser}" --open "%1"')

    assert launcher.default_browser_executable() == browser


def test_default_browser_executable_parses_unquoted_ordinary_path(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / "browser.exe"
    browser.touch()
    install_registry(monkeypatch, f"{browser} --open %1")

    assert launcher.default_browser_executable() == browser


def test_default_browser_executable_expands_percent_environment_variable(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / "Env Browser" / "browser.exe"
    browser.parent.mkdir()
    browser.touch()
    monkeypatch.setenv("LAUNCHER_BROWSER_DIR", str(browser.parent))
    install_registry(monkeypatch, '"%LAUNCHER_BROWSER_DIR%\\browser.exe" "%1"')

    assert launcher.default_browser_executable() == browser


def test_default_browser_executable_returns_none_when_user_choice_key_is_missing(monkeypatch):
    launcher = load_web_launcher()
    registry = install_registry(monkeypatch, '"C:\\browser.exe" "%1"')
    registry.missing.add((registry.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"))

    assert launcher.default_browser_executable() is None


def test_default_browser_executable_returns_none_when_executable_is_missing(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    missing = tmp_path / "not-installed.exe"
    install_registry(monkeypatch, f'"{missing}" "%1"')

    assert launcher.default_browser_executable() is None


@pytest.mark.parametrize("command", ["", "   ", '"unterminated path', '"" "%1"'])
def test_default_browser_executable_returns_none_for_empty_or_malformed_command(
        monkeypatch, command):
    launcher = load_web_launcher()
    install_registry(monkeypatch, command)

    assert launcher.default_browser_executable() is None
