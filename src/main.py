"""Точка входа SteamSNM."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.main_window import MainWindow, resource_path

SERVER_NAME = "SteamSNM"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SteamSNM")
    app.setOrganizationName("SteamSNM")
    app.setApplicationDisplayName("SteamSNM")

    icon = resource_path("icon.png")
    if icon:
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(icon))

    # Не допускаем запуск второй копии клиента.
    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(250):
        # Уже запущенная копия получит newConnection и покажет своё окно.
        probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(SERVER_NAME)

    window = MainWindow()
    window.show()

    def focus_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    server = QLocalServer()
    server.listen(SERVER_NAME)
    server.newConnection.connect(focus_window)

    def excepthook(kind, value, tb) -> None:  # noqa: ANN001
        message = "".join(traceback.format_exception(kind, value, tb))
        print(message, file=sys.stderr)
        QMessageBox.critical(None, "SteamSNM", f"Непредвиденная ошибка:\n{message[:1200]}")

    sys.excepthook = excepthook
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
