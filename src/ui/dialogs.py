"""Диалоги настроек, добавления игры и «О программе»."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from steam_api import CURRENCY_REGIONS, SearchResult, SteamAPI, format_money
from storage import Settings

# (подпись, код валюты, регион Steam cc, язык)
CURRENCIES = [
    (label, code, cc, lang)
    for code, (cc, lang, label) in CURRENCY_REGIONS.items()
]

AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "SteamSNM.desktop"


def set_autostart(enabled: bool) -> None:
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    if enabled:
        executable = Path(sys.argv[0]).resolve()
        AUTOSTART_FILE.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=SteamSNM\n"
            "Comment=Мониторинг цен и скидок Steam\n"
            f"Exec={executable}\n"
            "Icon=steamsnm\n"
            "Terminal=false\n"
            "Categories=Network;Utility;\n",
            encoding="utf-8",
        )
    elif AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink(missing_ok=True)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Настройки — SteamSNM")
        self.setMinimumWidth(460)

        self.currency = QComboBox()
        for label, code, cc, lang in CURRENCIES:
            self.currency.addItem(f"{label}  ·  {code}", (code, cc, lang))
        current = (settings.get("currency") or "RUB").upper()
        for index in range(self.currency.count()):
            if self.currency.itemData(index)[0] == current:
                self.currency.setCurrentIndex(index)
                break

        self.interval = QSpinBox()
        self.interval.setRange(1, 240)
        self.interval.setSuffix(" мин")
        self.interval.setValue(int(settings.get("interval_min")))

        self.auto_refresh = QCheckBox("Автоматически обновляться")
        self.auto_refresh.setChecked(bool(settings.get("auto_refresh")))
        self.fetch_players = QCheckBox("Запрашивать онлайн игроков")
        self.fetch_players.setChecked(bool(settings.get("fetch_players")))
        self.show_releases = QCheckBox("Показывать новинки Steam")
        self.show_releases.setChecked(bool(settings.get("show_releases")))
        self.minimize_tray = QCheckBox("Сворачивать в трей при закрытии")
        self.minimize_tray.setChecked(bool(settings.get("minimize_to_tray")))
        self.autostart = QCheckBox("Запускать при входе в систему")
        self.autostart.setChecked(bool(settings.get("autostart")))

        self.notify_discounts = QCheckBox("Скидки появились/увеличились")
        self.notify_discounts.setChecked(bool(settings.get("notify_discounts")))
        self.notify_price = QCheckBox("Снижение цены")
        self.notify_price.setChecked(bool(settings.get("notify_price_drop")))
        self.notify_target = QCheckBox("Достигнута целевая цена")
        self.notify_target.setChecked(bool(settings.get("notify_target")))
        self.notify_releases = QCheckBox("Новые релизы")
        self.notify_releases.setChecked(bool(settings.get("notify_releases")))

        form = QFormLayout()
        form.addRow("Валюта:", self.currency)
        form.addRow("Интервал обновления:", self.interval)
        form.addRow(self.auto_refresh)

        notification_box = QWidget()
        notify_layout = QVBoxLayout(notification_box)
        notify_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("<b>Уведомления</b>")
        notify_layout.addWidget(label)
        for checkbox in (
            self.notify_discounts, self.notify_price, self.notify_target, self.notify_releases
        ):
            notify_layout.addWidget(checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.fetch_players)
        layout.addWidget(self.show_releases)
        layout.addWidget(self.minimize_tray)
        layout.addWidget(self.autostart)
        layout.addWidget(notification_box)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def apply(self) -> None:
        code, cc, lang = self.currency.currentData()
        self.settings.update({
            "currency": code,
            "cc": cc,
            "lang": lang,
            "interval_min": self.interval.value(),
            "auto_refresh": self.auto_refresh.isChecked(),
            "fetch_players": self.fetch_players.isChecked(),
            "show_releases": self.show_releases.isChecked(),
            "minimize_to_tray": self.minimize_tray.isChecked(),
            "autostart": self.autostart.isChecked(),
            "notify_discounts": self.notify_discounts.isChecked(),
            "notify_price_drop": self.notify_price.isChecked(),
            "notify_target": self.notify_target.isChecked(),
            "notify_releases": self.notify_releases.isChecked(),
        })
        set_autostart(self.autostart.isChecked())

    def accept(self) -> None:
        self.apply()
        super().accept()


class AddGameDialog(QDialog):
    """Поиск игры через storesearch + добавление по AppID."""

    def __init__(self, api: SteamAPI, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.results: list[SearchResult] = []
        self._task = None
        self._pixmaps: dict[str, QPixmap] = {}
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._image_ready)

        self.setWindowTitle("Добавить игру — SteamSNM")
        self.setMinimumSize(520, 460)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск игры в Steam…")
        self.search_edit.setClearButtonEnabled(True)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(lambda _item: self.accept())

        self.status = QLabel("Введите название игры (минимум 2 символа).")
        self.status.setObjectName("muted")

        self.by_id_button = QPushButton("Добавить по AppID…")
        self.by_id_button.clicked.connect(self._add_by_id)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.add_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Добавить")
        self.add_button.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(self.search_edit)
        top.addWidget(self.by_id_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.results_list)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._do_search)
        self.search_edit.textChanged.connect(lambda _text: self._debounce.start())
        self.results_list.itemSelectionChanged.connect(
            lambda: self.add_button.setEnabled(bool(self.results_list.currentRow() >= 0))
        )

    def _do_search(self) -> None:
        term = self.search_edit.text().strip()
        if len(term) < 2:
            self.results.clear()
            self.results_list.clear()
            self.status.setText("Введите название игры (минимум 2 символа).")
            return
        if self._task is not None and self._task.isRunning():
            self._task.requestInterruption()
        from monitor import ApiTask

        self.status.setText("Поиск…")
        self._task = ApiTask(self.api.search, term, parent=self)
        self._task.finished_result.connect(self._show_results)
        self._task.failed.connect(lambda message: self.status.setText(f"Ошибка поиска: {message}"))
        self._task.start()

    def _show_results(self, results: list[SearchResult]) -> None:
        self.results = results
        self.results_list.clear()
        for result in results:
            price = format_money(result.price, result.currency) if result.price is not None else "—"
            item = QListWidgetItem(f"{result.name}\n{price}   •   AppID {result.appid}")
            item.setData(Qt.ItemDataRole.UserRole, result.appid)
            self.results_list.addItem(item)
            if result.image:
                pixmap = self._pixmaps.get(result.image)
                if pixmap is None:
                    request = QNetworkRequest(result.image)
                    reply = self._manager.get(request)
                    reply.setProperty("image_url", result.image)
                    reply.setProperty("item_row", self.results_list.count() - 1)
                else:
                    item.setIcon(QIcon(pixmap))
        self.status.setText(f"Найдено: {len(results)}. Двойной клик добавит игру сразу.")

    def _image_ready(self, reply: QNetworkReply) -> None:
        url = reply.property("image_url")
        row = reply.property("item_row")
        data = bytes(reply.readAll())
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            if url:
                self._pixmaps[url] = pixmap
            item = self.results_list.item(int(row)) if row is not None else None
            if item is not None:
                item.setIcon(QIcon(pixmap.scaled(46, 22, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation)))
        reply.deleteLater()

    def _add_by_id(self) -> None:
        value, ok = QInputDialog.getText(self, "Добавить по AppID", "Введите AppID игры:")
        if not ok:
            return
        raw = value.strip()
        if not raw.isdigit():
            QMessageBox.warning(self, "SteamSNM", "AppID должен быть числом.")
            return
        self._pending_manual = int(raw)
        self.accept()

    def selected(self) -> list[tuple[int, str, str]]:
        """Возвращает список (appid, name, image)."""
        row = self.results_list.currentRow()
        if 0 <= row < len(self.results):
            result = self.results[row]
            return [(result.appid, result.name, result.image)]
        manual = getattr(self, "_pending_manual", None)
        if manual:
            return [(manual, f"App {manual}", "")]
        return []

    @staticmethod
    def import_ids(parent=None) -> list[int]:
        text, ok = QInputDialog.getMultiLineText(
            parent,
            "Импорт AppID",
            "Вставьте AppID или ссылки Steam (по одному в строке):",
        )
        if not ok:
            return []
        import re

        ids: list[int] = []
        for token in re.split(r"[\s,;]+", text):
            match = re.search(r"(?:app/|appid=|^)(\d{3,})", token)
            if match:
                ids.append(int(match.group(1)))
        return list(dict.fromkeys(ids))


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("О программе — SteamSNM")
        text = (
            "<h2>SteamSNM</h2>"
            "<p><b>Steam Notifier &amp; Monitor</b> — десктопный клиент для мониторинга "
            "цен, скидок и онлайна игр в Steam.</p>"
            "<p>Данные берутся из официальных публичных Steam Store API и Steam Web API. "
            "Программа не парсит и не использует SteamDB.</p>"
            "<p>История цен, целевые цены и уведомления хранятся локально "
            "в <code>~/.config/SteamSNM</code>.</p>"
            "<p>Автор: <a href='https://github.com/skayinray'>skayinray</a> "
            "· <a href='https://github.com/skayinray/SteamSNM'>исходный код SteamSNM</a></p>"
            "<p>Версия 1.0 • Linux AppImage • PySide6</p>"
        )
        label = QLabel(text)
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)
