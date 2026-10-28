"""双击启动独立的物料计数桌面界面。"""
import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LOG_DIR = ROOT / ".runtime"


def main():
    if os.name == "nt" and PYTHONW.is_file() and Path(sys.executable).resolve() != PYTHONW.resolve():
        subprocess.Popen([str(PYTHONW), str(Path(__file__).resolve())], cwd=str(ROOT),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(filename=LOG_DIR / "desktop-gui.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    sys.path.insert(0, str(ROOT / "scripts" / "desktop_gui"))
    from controller import DesktopController, load_config
    from fluent_theme import FluentThemeManager
    from view import DesktopWindow, QtRootAdapter
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    theme = FluentThemeManager(app)
    theme.apply("system")
    root = QtRootAdapter()
    controller = DesktopController(root, load_config())
    view = DesktopWindow(controller, theme)
    root.bind(view)
    controller.attach(view)
    view.show()
    QTimer.singleShot(100, controller.start)
    app.exec()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG_DIR.mkdir(exist_ok=True)
        error = traceback.format_exc()
        with (LOG_DIR / "desktop-gui.log").open("a", encoding="utf-8") as log:
            log.write(error + "\n")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, error[-2000:], "物料计数 GUI 启动失败", 0x10)
        else:
            raise
