"""Главное окно SteamSNM: каталог Steam, страницы игр, отслеживание, уведомления."""

from __future__ import annotations

import ctypes
import html
import math
import os
import re
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import (
    QDateTime, QObject, QPointF, QSignalBlocker, QSize, Qt, QTimer, QUrl,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap,
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDockWidget, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QStatusBar, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from steam_api import CURRENCY_REGIONS, GameInfo, SteamAPI, currency_region, format_money
from storage import CatalogCache, Settings, Store, Watchlist
from monitor import ApiTask, BulkCatalogWorker, CatalogWorker, MonitorWorker
from ui.dialogs import AboutDialog, SettingsDialog


def resource_path(name: str) -> str:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "resources" / name,
        here.parent / "resources" / name,
        Path("/usr/share/steamsnm/resources") / name,
        Path(os.environ.get("APPDIR", "/")) / "usr/share/steamsnm/resources" / name,
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


def capsule_url(appid: int) -> str:
    """Надёжный адрес иконки игры (старый CDN Steam без хэшей)."""
    return f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/capsule_231x87.jpg"


def header_url(appid: int) -> str:
    """Надёжный адрес обложки игры для страницы."""
    return f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/header.jpg"


def fastly_url(url: str) -> str:
    """Скриншоты Steam отдаёт с fastly; старый akamai-хост часто недоступен."""
    return url.replace("shared.akamai.steamstatic.com", "shared.fastly.steamstatic.com")


def plain_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(html.unescape(text).split())


def trim_memory() -> None:
    """Вернуть glibc свободные страницы ОС (долгая работа в трее)."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # не Linux или другая libc — просто игнорируем


class SortItem(QTableWidgetItem):
    """Элемент таблицы с числовой сортировкой."""

    def __init__(self, text: str, value: float = 0.0) -> None:
        super().__init__(text)
        self._sort_value = value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


class ImageLoader(QObject):
    """Асинхронная загрузка изображений с ограниченным LRU-кэшем.

    Полноразмерные обложки Steam весят по 200-500 КБ. Без жёсткого лимита
    кэш неограниченно рос при листании каталога — приложение «съедало»
    сотни МБ и плохо подходило для постоянной работы в трее.
    """

    MAX_CACHE = 48
    MAX_PIXMAP_SIDE = 900

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._finished)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._callbacks: dict[str, list] = {}

    def load(self, url: str, callback) -> None:
        if not url:
            return
        pixmap = self._cache.get(url)
        if pixmap is not None:
            self._cache.move_to_end(url)
            callback(pixmap)
            return
        if url in self._callbacks:
            self._callbacks[url].append(callback)
            return
        self._callbacks[url] = [callback]
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._manager.get(request)
        reply.setProperty("image_url", url)

    def _finished(self, reply: QNetworkReply) -> None:
        url = reply.property("image_url") or ""
        data = bytes(reply.readAll())
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            # Храним уменьшенную копию: для таблицы (120x45), скриншотов
            # (174x82) и баннера (до ~800px) этого более чем достаточно.
            if pixmap.width() > self.MAX_PIXMAP_SIDE or pixmap.height() > self.MAX_PIXMAP_SIDE:
                pixmap = pixmap.scaled(
                    self.MAX_PIXMAP_SIDE, self.MAX_PIXMAP_SIDE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._cache[url] = pixmap
            self._cache.move_to_end(url)
            while len(self._cache) > self.MAX_CACHE:
                self._cache.popitem(last=False)
        for callback in self._callbacks.pop(url, []):
            try:
                callback(pixmap)
            except (RuntimeError, TypeError):
                pass
        reply.deleteLater()

    def clear(self) -> None:
        """Освободить память картинок (при уходе в трей/ выходе)."""
        self._cache.clear()
        self._callbacks.clear()


SECTION_LABELS = {
    "specials": "Скидки",
    "new_releases": "Новинки",
    "top_sellers": "Топ продаж",
    "coming_soon": "Скоро",
    "popular": "Популярные",
    "catalog": "Весь мир",
}

SECTION_ORDER = {
    "top_sellers": 0, "popular": 1, "specials": 2,
    "new_releases": 3, "coming_soon": 4, "catalog": 5,
}

FILTERS = [
    ("all", "Все игры"),
    ("specials", "Скидки"),
    ("watchlist", "Отслеживаемые"),
    ("new_releases", "Новинки"),
    ("top_sellers", "Лидеры продаж"),
    ("popular", "Популярные"),
    ("coming_soon", "Скоро"),
    ("global", "Зарубежные"),
    ("free", "Бесплатные"),
]

PAGE_SIZE = 100


class GamePage(QWidget):
    """Страница игры внутри главного окна. Новые страницы ложатся поверх прошлых."""

    def __init__(self, game: GameInfo, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        self.appid = game.appid
        self.game = game

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 10, 12, 8)
        self.back_button = QPushButton("← Назад")
        self.back_button.clicked.connect(self.go_back)
        bar.addWidget(self.back_button)
        bar.addSpacing(12)
        title = QLabel(game.name)
        title.setObjectName("detailName")
        bar.addWidget(title, 1)
        self.track_button = QPushButton()
        self.track_button.clicked.connect(self._toggle_track)
        bar.addWidget(self.track_button)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(7)

        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setFixedHeight(180)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setText("Загрузка обложки…")
        layout.addWidget(self.banner)

        self.meta = QLabel("")
        self.meta.setObjectName("muted")
        layout.addWidget(self.meta)

        price_row = QHBoxLayout()
        self.price = QLabel("—")
        self.price.setObjectName("detailPrice")
        self.discount = QLabel("")
        self.discount.setObjectName("discountBadge")
        self.old_price = QLabel("")
        self.old_price.setObjectName("muted")
        price_row.addWidget(self.price)
        price_row.addWidget(self.discount)
        price_row.addWidget(self.old_price)
        price_row.addStretch(1)
        layout.addLayout(price_row)

        self.stats = QLabel("")
        self.stats.setObjectName("muted")
        layout.addWidget(self.stats)

        self.description = QLabel("")
        self.description.setObjectName("description")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        self.screenshots = QHBoxLayout()
        self.screenshots.setSpacing(6)
        layout.addLayout(self.screenshots)

        chart_title = QLabel("История цен (сохраняется локально)")
        chart_title.setObjectName("sectionTitle")
        layout.addWidget(chart_title)

        self.chart = QChart()
        self.chart.setTheme(QChart.ChartTheme.ChartThemeDark)
        self.chart.legend().hide()
        self.chart.setBackgroundVisible(False)
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_view.setMinimumHeight(150)
        layout.addWidget(self.chart_view, 1)

        buttons = QHBoxLayout()
        steam_button = QPushButton("Открыть в Steam")
        steam_button.clicked.connect(self._open_steam)
        online_button = QPushButton("Обновить онлайн")
        online_button.clicked.connect(self._refresh_players)
        target_button = QPushButton("Целевая цена…")
        target_button.clicked.connect(self._set_target)
        buttons.addWidget(steam_button)
        buttons.addWidget(online_button)
        buttons.addWidget(target_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        scroll.setWidget(page)
        root.addWidget(scroll, 1)
        self.render()

    def go_back(self) -> None:
        self.main.close_page(self)

    # ------------------------------------------------------------- datos ---

    def render(self) -> None:
        if not isValid(self) or self.main is None:
            return
        game = self.main.games.get(self.appid, self.game)
        self.game = game
        self.setObjectName(f"gamePage_{self.appid}")
        raw = self.main.detail_cache.get(self.appid, {})

        genres = [g.get("description", "") for g in (raw.get("genres") or []) if g.get("description")]
        developers = ", ".join(raw.get("developers") or [])
        release = (raw.get("release_date") or {}).get("date") or game.release_date
        self.meta.setText("  •  ".join(p for p in [release, developers, *genres[:5]] if p))

        # Зелёным выделяем только цену со скидкой, а не любую цену.
        self.price.setStyleSheet(
            "QLabel { color: %s; font-size: 25px; font-weight: bold; }"
            % ("#a4d007" if game.discount else "#e8f1fa")
        )
        self.price.setText(game.formatted_price)
        self.discount.setText(f"−{game.discount}%" if game.discount else "")
        self.old_price.setText(f"Было {game.formatted_initial}" if game.discount else "")

        online = "—" if game.players is None else f"{game.players:,}".replace(",", " ")
        stats = self.main.store.stats(self.appid)
        low = format_money(stats.get("low"), game.currency) if stats.get("low") is not None else "—"
        self.stats.setText(
            f"Онлайн: {online}   •   Рейтинг: {game.rating}   •   "
            f"Минимум: {low}   •   Наблюдений: {stats.get('n') or 0}"
        )
        self._update_track_button()

        description = raw.get("about_the_game") or raw.get("short_description") or ""
        self.description.setText(plain_text(description)[:1800] or "Загрузка описания…")

        self.main.images.load(
            header_url(self.appid),
            lambda pix: self.banner.setPixmap(
                pix.scaled(
                    self.banner.width(), self.banner.height(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            ) if not pix.isNull() else self.banner.setText("Нет обложки"),
        )

        self._clear_screenshots()
        for shot in (raw.get("screenshots") or [])[:5]:
            thumb = fastly_url(shot.get("path_thumbnail", ""))
            if not thumb:
                continue
            label = QLabel()
            label.setFixedSize(180, 88)
            label.setObjectName("banner")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setText("…")
            self.screenshots.addWidget(label)
            self.main.images.load(
                thumb,
                lambda pix, lbl=label: lbl.setPixmap(
                    pix.scaled(174, 82, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                ) if not pix.isNull() else None,
            )
        self.screenshots.addStretch(1)
        self._update_chart()

    def _clear_screenshots(self) -> None:
        while self.screenshots.count():
            item = self.screenshots.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _update_chart(self) -> None:
        for series in list(self.chart.series()):
            self.chart.removeSeries(series)
        for axis in list(self.chart.axes()):
            self.chart.removeAxis(axis)
        data = self.main.store.series(self.appid)
        if not data:
            self.chart.setTitle("Нет данных истории — появится после обновления цены")
            return
        self.chart.setTitle("")
        series = QLineSeries()
        series.setName("Цена")
        series.setColor(QColor("#66c0f4"))
        series.setPointsVisible(True)
        series.setMarkerSize(7)
        points = [(ts * 1000.0, price / 100.0) for ts, price in data]
        if len(points) == 1:
            # Одна точка линией не рисуется — продлеваем её на день.
            points.append((points[0][0] + 86_400_000, points[0][1]))
        for x, y in points:
            series.append(QPointF(x, y))
        axis_x = QDateTimeAxis()
        axis_x.setFormat("dd.MM.yy")
        axis_x.setTitleText("Дата")
        axis_x.setLabelsColor(QColor("#8ba3b8"))
        axis_x.setGridLineColor(QColor("#2c3e50"))
        axis_y = QValueAxis()
        axis_y.setTitleText("Цена")
        axis_y.setLabelsColor(QColor("#8ba3b8"))
        axis_y.setGridLineColor(QColor("#2c3e50"))
        self.chart.addSeries(series)
        self.chart.setAxisX(axis_x, series)
        self.chart.setAxisY(axis_y, series)
        first = QDateTime.fromMSecsSinceEpoch(data[0][0] * 1000)
        last = QDateTime.fromMSecsSinceEpoch(data[-1][0] * 1000)
        if first == last:
            last = last.addDays(1)
        axis_x.setRange(first, last)
        prices = [price / 100.0 for _, price in data]
        minimum, maximum = min(prices), max(prices)
        padding = max((maximum - minimum) * 0.15, 0.5)
        axis_y.setRange(max(0.0, minimum - padding), maximum + padding)

    def _update_track_button(self) -> None:
        tracked = self.main.watchlist.get(self.appid) is not None
        self.track_button.setText("★ Не отслеживать" if tracked else "☆ Отслеживать")
        self.track_button.setObjectName("primary" if tracked else "")

    def _toggle_track(self) -> None:
        self.main.toggle_track(self.appid)
        self._update_track_button()

    def _set_target(self) -> None:
        game = self.main.games.get(self.appid, self.game)
        current = (self.main.watchlist.get(self.appid) or {}).get("target_price")
        initial = (current or 0) / 100.0
        value, ok = QInputDialog.getDouble(
            self, "Целевая цена", f"Цена для {game.name} ({game.currency}):",
            initial, 0.0, 1_000_000.0, 2,
        )
        if ok:
            self.main.watchlist.set_target(self.appid, int(round(value * 100)) or None)
            self.main.status.showMessage("Целевая цена сохранена.")

    def _open_steam(self) -> None:
        QDesktopServices.openUrl(QUrl(f"https://store.steampowered.com/app/{self.appid}/"))

    def _refresh_players(self) -> None:
        self.main.fetch_players(self.appid)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.accept()
        if self.main is not None:
            self.main.page_closed(self)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.store = Store()
        # Отдельный файл: пользователи предыдущих сборок получат глобальный
        # каталог сразу, а не будут ждать истечения срока старого кэша.
        self.cache = CatalogCache("catalog_global.json")
        self.watchlist = Watchlist()
        # Валюта задаёт регион Steam: все цены приходят из Store API уже в ней.
        # По умолчанию (и для старых конфигов) это рубли.
        currency = (self.settings.get("currency") or "RUB").upper()
        if currency not in CURRENCY_REGIONS:
            currency = "RUB"
        cc, lang = currency_region(currency)
        self.settings.update({"currency": currency, "cc": cc, "lang": lang})
        self.api = SteamAPI(cc, lang)
        # Зарубежные игры загружаются из глобального поиска Steam (США/English),
        # чтобы каталог не ограничивался играми, доступными в выбранном регионе.
        self.global_api = SteamAPI("us", "english")

        self.games: dict[int, GameInfo] = {}
        # Полные JSON страниц игр тяжёлые — держим только недавно открытые.
        self.detail_cache: OrderedDict[int, dict] = OrderedDict()
        self.page_stack: list[GamePage] = []
        self.page = 0
        self.row_appids: list[int] = []
        self.selected_appid: int | None = None
        self.event_log: list[str] = []

        self.worker: CatalogWorker | None = None
        self.watch_worker: MonitorWorker | None = None
        self.bulk_worker: BulkCatalogWorker | None = None
        self.detail_task: ApiTask | None = None
        self.player_task: ApiTask | None = None
        self.detail_queue: list[int] = []
        self._force_quit = False

        cached = self.cache.load()
        if cached:
            for item in cached:
                try:
                    appid = int(item["appid"])
                except (KeyError, TypeError, ValueError):
                    continue
                self.games.setdefault(appid, GameInfo(
                    appid=appid,
                    name=item.get("name", f"App {appid}"),
                    image=item.get("image", ""),
                    section="catalog",
                ))

        icon_path = resource_path("icon.png")
        self.app_icon = QIcon(icon_path) if icon_path else QIcon()
        self.setWindowIcon(self.app_icon)
        self.setWindowTitle("SteamSNM — каталог Steam")
        self.resize(1340, 860)
        self.setMinimumSize(980, 640)

        self.images = ImageLoader(self)
        self._build_ui()
        self._build_tray()
        self._build_timer()
        self._apply_style()
        QTimer.singleShot(300, self.refresh)

    # ---------------------------------------------------------------- UI ---

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)

        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.app_icon.pixmap(34, 34))
        brand = QLabel("<b style='font-size:19px;'>Steam<span style='color:#66c0f4;'>SNM</span></b>")
        brand.setObjectName("brand")
        top.addWidget(logo)
        top.addWidget(brand)
        top.addSpacing(20)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по всему каталогу Steam…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        top.addWidget(self.search_edit, 1)

        self.summary = QLabel("Загрузка каталога…")
        self.summary.setObjectName("muted")
        top.addWidget(self.summary)
        top.addSpacing(8)

        page_button = QPushButton("Страница игры")
        page_button.clicked.connect(self.open_selected_page)
        top.addWidget(page_button)

        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setObjectName("primary")
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.refresh_button)
        root.addLayout(top)
        self.view_stack = QStackedWidget()
        self.table_view = self._build_table_area()
        self.view_stack.addWidget(self.table_view)
        root.addWidget(self.view_stack, 1)

        self.log_dock = QDockWidget("Журнал событий", self)
        self.log_dock.setObjectName("eventLog")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setVisible(False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Загрузка данных Steam…")
        self._build_menu()

    def _build_table_area(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.filter_list = QListWidget()
        self.filter_list.setFixedWidth(210)
        for key, label in FILTERS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.filter_list.addItem(item)
        self.filter_list.setCurrentRow(0)
        self.filter_list.currentRowChanged.connect(self._on_filter_changed)
        layout.addWidget(self.filter_list)

        right = QVBoxLayout()
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Игра", "Цена", "Было", "Скидка", "Онлайн", "Минимум", "Раздел"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setIconSize(QSize(120, 45))
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(self.open_selected_page)
        right.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.prev_button = QPushButton("‹ Назад")
        self.prev_button.clicked.connect(self._prev_page)
        self.next_button = QPushButton("Вперёд ›")
        self.next_button.clicked.connect(self._next_page)
        self.page_label = QLabel("Страница 1")
        self.page_label.setObjectName("muted")
        self.bulk_label = QLabel("")
        self.bulk_label.setObjectName("muted")
        pager.addWidget(self.prev_button)
        pager.addWidget(self.next_button)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        pager.addWidget(self.bulk_label)
        right.addLayout(pager)
        layout.addLayout(right, 1)
        return widget

    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("Файл")
        action = QAction("Настройки…", self)
        action.setShortcut(QKeySequence("Ctrl+,"))
        action.triggered.connect(self.open_settings)
        file_menu.addAction(action)
        action = QAction("Выход", self)
        action.setShortcut(QKeySequence("Ctrl+Q"))
        action.triggered.connect(self.quit_app)
        file_menu.addAction(action)

        view_menu = menu.addMenu("Вид")
        self.log_action = QAction("Журнал событий", self)
        self.log_action.setCheckable(True)
        self.log_action.toggled.connect(self.log_dock.setVisible)
        view_menu.addAction(self.log_action)
        action = QAction("Обновить", self)
        action.setShortcut(QKeySequence("Ctrl+R"))
        action.triggered.connect(self.refresh)
        view_menu.addAction(action)
        action = QAction("Открыть страницу игры", self)
        action.setShortcut(QKeySequence("Return"))
        action.triggered.connect(self.open_selected_page)
        view_menu.addAction(action)

        help_menu = menu.addMenu("Справка")
        action = QAction("О программе", self)
        action.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(action)
        action = QAction("GitHub автора (skayinray)", self)
        action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/skayinray"))
        )
        help_menu.addAction(action)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip("SteamSNM — каталог Steam")
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Показать окно")
        show_action.triggered.connect(self._show_window)
        refresh_action = tray_menu.addAction("Обновить сейчас")
        refresh_action.triggered.connect(self.refresh)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Выход")
        quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _build_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._restart_timer()

    def _restart_timer(self) -> None:
        if self.settings.get("auto_refresh"):
            self.timer.start(self.settings.interval_ms)
        else:
            self.timer.stop()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { color: #d8e2ec; font-size: 13px; }
            QMainWindow, QDialog, QWidget#window { background: #121b26; }
            QMenuBar { background: #1b2838; border-bottom: 1px solid #2c3e50; }
            QMenuBar::item:selected { background: #2a475e; }
            QMenu { background: #1b2838; border: 1px solid #3c5266; }
            QMenu::item:selected { background: #2a475e; }
            QTableWidget { background: #1b2838; alternate-background-color: #172231;
                gridline-color: #2c3e50; border: 1px solid #2c3e50; border-radius: 6px; }
            QTableWidget::item { padding: 3px; }
            QTableWidget::item:selected { background: #2a475e; }
            QHeaderView::section { background: #223348; padding: 7px; border: none;
                border-right: 1px solid #2c3e50; font-weight: bold; }
            QListWidget { background: #1b2838; border: 1px solid #2c3e50; border-radius: 6px; }
            QListWidget::item { padding: 9px; border-bottom: 1px solid #243446; }
            QListWidget::item:selected { background: #2a475e; color: #fff; font-weight: bold; }
            QLineEdit { background: #101b27; border: 1px solid #34495e;
                border-radius: 6px; padding: 8px; }
            QPushButton { background: #2a475e; border: 1px solid #3c617e;
                border-radius: 6px; padding: 8px 14px; }
            QPushButton:hover { background: #345d7c; }
            QPushButton#primary { background: #1a9fff; color: #fff;
                border-color: #43b4ff; font-weight: bold; }
            QPushButton#primary:hover { background: #34adff; }
            QLabel#muted { color: #8ba3b8; }
            QLabel#brand { color: #fff; }
            QLabel#detailName { font-size: 17px; font-weight: bold; color: #fff; }
            QLabel#detailPrice { font-size: 25px; font-weight: bold; color: #e8f1fa; }
            QLabel#discountBadge { color: #fff; background: #4c9c2b;
                border-radius: 5px; padding: 4px 9px; font-weight: bold; }
            QLabel#banner { background: #101b27; border: 1px solid #2c3e50;
                border-radius: 6px; color: #546b80; }
            QLabel#description { color: #b8c9d9; }
            QLabel#sectionTitle { color: #66c0f4; font-weight: bold; margin-top: 6px; }
            QScrollArea { background: #16202d; border: none; }
            QStatusBar { color: #8ba3b8; }
            QDockWidget { background: #16202d; }
            """
        )
        for page in self.page_stack:
            page.setStyleSheet(self.styleSheet())

    # ------------------------------------------------------------- data ---

    def refresh(self) -> None:
        if self.worker is not None and isValid(self.worker) and self.worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Обновление…")
        self.worker = CatalogWorker(self.api, self.store, self.settings, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.updated.connect(self._on_updated)
        self.worker.events.connect(self._on_events)
        self.worker.releases.connect(self._on_releases)
        self.worker.failed.connect(self._on_failed)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

        self._start_watch_check()
        if (
            self.cache.age_days() > 2
            and (self.bulk_worker is None or not isValid(self.bulk_worker)
                 or not self.bulk_worker.isRunning())
        ):
            self._start_bulk_load()

    def _start_watch_check(self) -> None:
        ids = self.watchlist.appids()
        if not ids:
            return
        if self.watch_worker is not None and isValid(self.watch_worker) and self.watch_worker.isRunning():
            return
        self.watch_worker = MonitorWorker(
            self.api, self.watchlist, self.store, self.settings, self
        )
        self.watch_worker.progress.connect(self._on_watch_progress)
        self.watch_worker.updated.connect(self._on_watch_updated)
        self.watch_worker.events.connect(self._on_events)
        self.watch_worker.failed.connect(self._on_watch_failed)
        self.watch_worker.finished.connect(self._on_watch_finished)
        self.watch_worker.start()

    def _start_bulk_load(self) -> None:
        # Глобальный поиск (США/английский) —includes игры, недоступные в RU.
        self.bulk_worker = BulkCatalogWorker(self.global_api, pages=200, parent=self)
        self.bulk_worker.batch.connect(self._on_bulk_batch)
        self.bulk_worker.progress.connect(self._on_bulk_progress)
        self.bulk_worker.completed.connect(self._on_bulk_completed)
        self.bulk_worker.failed.connect(self._on_bulk_failed)
        self.bulk_worker.start()
        self.status.showMessage("Загрузка полного каталога Steam (весь мир)…")

    def _on_progress(self, message: str) -> None:
        self.status.showMessage(message)

    def _on_watch_progress(self, message: str, done: int, total: int) -> None:
        self.status.showMessage(f"Отслеживание: {message} ({done}/{total})")

    def _on_watch_updated(self, games: list[GameInfo]) -> None:
        for game in games:
            if game.appid in self.games:
                current = self.games[game.appid]
                current.price = game.price
                current.initial = game.initial
                current.currency = game.currency
                current.discount = game.discount
                current.formatted_price = game.formatted_price
                current.formatted_initial = game.formatted_initial
                current.free = game.free
                current.updated = game.updated or int(time.time())
        self.refresh_table()
        for page in list(self.page_stack):
            if isValid(page) and page.appid in {g.appid for g in games}:
                page.render()

    def _on_watch_failed(self, message: str) -> None:
        self._log(f"[!] Отслеживание: {message}")

    def _on_watch_finished(self) -> None:
        if self.watch_worker is not None and isValid(self.watch_worker):
            self.watch_worker.deleteLater()
        self.watch_worker = None

    def _on_updated(self, games: list[GameInfo]) -> None:
        for appid in [a for a, g in self.games.items() if g.section != "catalog"]:
            self.games.pop(appid, None)
        for game in games:
            self.games[game.appid] = game
        self.refresh_table()
        for page in list(self.page_stack):
            if isValid(page):
                page.render()

    def _save_catalog_cache(self) -> None:
        """Сохранить каталог из основного словаря, не дублируя его в памяти."""
        self.cache.save(
            {"appid": game.appid, "name": game.name, "image": game.image}
            for game in self.games.values() if game.section == "catalog"
        )

    def _on_bulk_batch(self, games: list[GameInfo]) -> None:
        for game in games:
            self.games.setdefault(game.appid, game)

    def _on_bulk_progress(self, pages: int, total: int) -> None:
        self.bulk_label.setText(f"Каталог: {total:,}".replace(",", " "))
        self.status.showMessage(f"Загружено страниц каталога: {pages} • Игр: {total}")
        if pages % 10 == 0:
            self._save_catalog_cache()
            self.refresh_table()

    def _on_bulk_completed(self, total: int) -> None:
        self._save_catalog_cache()
        self.bulk_label.setText(f"Каталог: {total:,}".replace(",", " "))
        self.status.showMessage(f"Каталог Steam загружен: {total} игр.")
        self.refresh_table()
        trim_memory()

    def _on_bulk_failed(self, message: str) -> None:
        self.bulk_label.setText("Каталог: частично")
        self.status.showMessage(f"Каталог загружен частично: {message}")

    def _on_events(self, messages: list[str]) -> None:
        for message in messages:
            line = f"[{time.strftime('%H:%M:%S')}] {message}"
            self.event_log.append(line)
            self.log_view.appendPlainText(line)
        if messages and self.tray.isVisible():
            preview = "\n".join(messages[:3])
            if len(messages) > 3:
                preview += f"\n… и ещё {len(messages) - 3}"
            self.tray.showMessage(
                "SteamSNM", preview, QSystemTrayIcon.MessageIcon.Information, 9000
            )

    def _on_releases(self, items: list[GameInfo]) -> None:
        """Новинки уже есть в витрине — отдельно ничего делать не нужно."""

    def _on_failed(self, message: str) -> None:
        self.status.showMessage(f"Ошибка: {message}", 12000)
        self._log(f"[!] Ошибка обновления: {message}")

    def _on_succeeded(self, timestamp: float) -> None:
        self.status.showMessage(
            f"Обновлено в {time.strftime('%H:%M:%S', time.localtime(timestamp))} • "
            f"Игр в каталоге: {len(self.games):,} • следующее обновление через "
            f"{self.settings.get('interval_min')} мин.".replace(",", " ")
        )

    def _on_worker_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Обновить")
        if self.worker is not None and isValid(self.worker):
            self.worker.deleteLater()
        self.worker = None

    def _log(self, line: str) -> None:
        self.event_log.append(line)
        self.log_view.appendPlainText(line)

    # ------------------------------------------------------------- table ---

    def _current_filter(self) -> str:
        row = self.filter_list.currentRow()
        return FILTERS[row][0] if 0 <= row < len(FILTERS) else "all"

    def _visible_games(self) -> list[GameInfo]:
        games = list(self.games.values())
        key = self._current_filter()
        if key == "specials":
            games = [g for g in games if g.discount > 0]
        elif key == "free":
            games = [g for g in games if g.free]
        elif key == "watchlist":
            ids = set(self.watchlist.appids())
            games = [g for g in games if g.appid in ids]
        elif key == "global":
            games = [g for g in games if g.section == "catalog"]
        elif key in ("new_releases", "top_sellers", "coming_soon", "popular"):
            games = [g for g in games if g.section == key]
        term = self.search_edit.text().strip().lower()
        if term:
            games = [g for g in games if term in g.name.lower()]
        return sorted(
            games,
            key=lambda g: (SECTION_ORDER.get(g.section, 9), -g.discount, g.name.lower()),
        )

    def _on_search_changed(self) -> None:
        self.page = 0
        self.refresh_table()

    def _on_filter_changed(self) -> None:
        self.page = 0
        self.refresh_table()

    def _prev_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.refresh_table()

    def _next_page(self) -> None:
        self.page += 1
        self.refresh_table()

    def refresh_table(self) -> None:
        games = self._visible_games()
        pages = max(1, math.ceil(len(games) / PAGE_SIZE))
        self.page = min(max(0, self.page), pages - 1)
        chunk = games[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        self.row_appids = [g.appid for g in chunk]

        previous = self.selected_appid
        with QSignalBlocker(self.table):
            self.table.setSortingEnabled(False)
            self.table.clearContents()
            self.table.setRowCount(len(chunk))
        for row, game in enumerate(chunk):
            name_item = QTableWidgetItem(game.name)
            name_item.setData(Qt.ItemDataRole.UserRole, game.appid)
            self.table.setItem(row, 0, name_item)
            self._load_row_icon(game)
            price_item = SortItem(game.formatted_price, game.price or 0)
            price_item.setForeground(QColor("#d8e2ec"))
            self.table.setItem(row, 1, price_item)
            old_item = SortItem(game.formatted_initial, game.initial or 0)
            old_item.setForeground(QColor("#8ba3b8"))
            self.table.setItem(row, 2, old_item)

            discount = f"−{game.discount}%" if game.discount else "—"
            discount_item = SortItem(discount, game.discount)
            if game.discount:
                discount_item.setForeground(QColor("#a4d007"))
                font = discount_item.font()
                font.setBold(True)
                discount_item.setFont(font)
            self.table.setItem(row, 3, discount_item)

            players = "—" if game.players is None else f"{game.players:,}".replace(",", " ")
            self.table.setItem(row, 4, SortItem(players, game.players or 0))

            stats = self.store.stats(game.appid)
            low = stats.get("low")
            self.table.setItem(
                row, 5,
                SortItem(format_money(low, game.currency) if low is not None else "—", low or 0),
            )
            section = SECTION_LABELS.get(game.section, "—")
            if self.watchlist.get(game.appid):
                section = "★ " + section
            self.table.setItem(row, 6, QTableWidgetItem(section))
        self.table.setSortingEnabled(True)

        discounted = sum(1 for g in games if g.discount > 0)
        self.summary.setText(
            f"Найдено: {len(games):,}  •  Со скидкой: {discounted:,}".replace(",", " ")
        )
        self.page_label.setText(
            f"Страница {self.page + 1} из {pages}  •  показано {len(chunk)} из {len(games):,}"
            .replace(",", " ")
        )
        if not self.bulk_label.text():
            self.bulk_label.setText(f"Всего в каталоге: {len(self.games):,}".replace(",", " "))
        for index, (key, label) in enumerate(FILTERS):
            self.filter_list.item(index).setText(f"{label}  ({self._count_for_filter(key)})")
        with QSignalBlocker(self.table):
            if previous in self.row_appids:
                self.table.selectRow(self.row_appids.index(previous))
            elif previous is not None:
                self.selected_appid = None

    def _count_for_filter(self, key: str) -> int:
        games = list(self.games.values())
        if key == "specials":
            return sum(1 for g in games if g.discount > 0)
        if key == "free":
            return sum(1 for g in games if g.free)
        if key == "watchlist":
            ids = set(self.watchlist.appids())
            return sum(1 for g in games if g.appid in ids)
        if key == "global":
            return sum(1 for g in games if g.section == "catalog")
        if key in ("new_releases", "top_sellers", "coming_soon", "popular"):
            return sum(1 for g in games if g.section == key)
        return len(games)

    def _load_row_icon(self, game: GameInfo) -> None:
        primary = capsule_url(game.appid)
        fallback = fastly_url(game.image)

        def apply(pixmap: QPixmap) -> None:
            if pixmap.isNull():
                if fallback and fallback != primary:
                    self.images.load(
                        fallback,
                        lambda other: self._set_row_icon(game.appid, other),
                    )
                return
            self._set_row_icon(game.appid, pixmap)

        self.images.load(primary, apply)

    def _set_row_icon(self, appid: int, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            120, 45, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and int(item.data(Qt.ItemDataRole.UserRole) or 0) == appid:
                item.setIcon(QIcon(scaled))
                break

    # ------------------------------------------------------- game pages ---

    def _selected_game(self) -> GameInfo | None:
        items = self.table.selectedItems()
        if not items:
            return None
        appid = int(self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole) or 0)
        return self.games.get(appid)

    def _on_selection_changed(self) -> None:
        game = self._selected_game()
        self.selected_appid = game.appid if game else None

    def open_selected_page(self) -> None:
        game = self._selected_game()
        if game:
            self.open_game_page(game)

    def open_game_page(self, game: GameInfo) -> None:
        for page in list(self.page_stack):
            if isValid(page) and page.appid == game.appid:
                self.view_stack.setCurrentWidget(page)
                return
        page = GamePage(game, self)
        page.setStyleSheet(self.styleSheet())
        self.view_stack.addWidget(page)
        self.page_stack.append(page)
        self.view_stack.setCurrentWidget(page)
        # Первая точка истории — сразу при открытии страницы, чтобы график
        # не был пустым до следующего фонового опроса.
        if game.price is not None and not self.store.series(game.appid):
            self.store.record(game)
        if game.appid not in self.detail_cache and (
            self.detail_task is None or not isValid(self.detail_task)
            or not self.detail_task.isRunning()
        ):
            self.fetch_details(game.appid)
        if game.players is None and self.settings.get("fetch_players"):
            self.fetch_players(game.appid)

    def close_page(self, page: GamePage) -> None:
        """Закрыть текущую страницу и вернуться к предыдущей/списку."""
        if isValid(page):
            if page in self.page_stack:
                self.page_stack.remove(page)
            index = self.view_stack.indexOf(page)
            if index >= 0:
                self.view_stack.removeWidget(page)
            page.deleteLater()
        elif page in self.page_stack:
            self.page_stack.remove(page)
        previous = self.page_stack[-1] if self.page_stack else self.table_view
        self.view_stack.setCurrentWidget(previous)

    page_closed = close_page

    def _next_detail(self) -> None:
        if self.detail_queue:
            next_appid = self.detail_queue.pop(0)
            QTimer.singleShot(0, lambda: self.fetch_details(next_appid, queued=True))

    def fetch_details(self, appid: int, *, queued: bool = False) -> None:
        if self.detail_task is not None and isValid(self.detail_task) and self.detail_task.isRunning():
            if not queued and appid not in self.detail_queue:
                self.detail_queue.append(appid)
            return

        def done(data: dict) -> None:
            self.detail_cache[appid] = data or {}
            self.detail_cache.move_to_end(appid)
            while len(self.detail_cache) > 24:
                self.detail_cache.popitem(last=False)
            parsed = self.api.parse_details(appid, data or {})
            if parsed is not None and appid in self.games:
                current = self.games[appid]
                current.price = parsed.price
                current.initial = parsed.initial
                current.currency = parsed.currency
                current.discount = parsed.discount
                current.formatted_price = parsed.formatted_price
                current.formatted_initial = parsed.formatted_initial
                current.free = parsed.free
                current.release_date = parsed.release_date
                current.genres = parsed.genres
                current.positive = parsed.positive
                current.total_reviews = parsed.total_reviews
                current.updated = parsed.updated or int(time.time())
                self.store.record(current)
                self.refresh_table()
            for page in list(self.page_stack):
                if isValid(page) and page.appid == appid:
                    page.render()
            if self.detail_task is task:
                self.detail_task = None
            task.deleteLater()
            self._next_detail()

        def failed(message: str) -> None:
            self.status.showMessage(f"Страница игры: {message}")
            if self.detail_task is task:
                self.detail_task = None
            task.deleteLater()
            self._next_detail()

        task = ApiTask(self.api.app_details_full, appid, parent=self)
        task.finished_result.connect(done)
        task.failed.connect(failed)
        self.detail_task = task
        task.start()

    def fetch_players(self, appid: int) -> None:
        if self.player_task is not None and isValid(self.player_task) and self.player_task.isRunning():
            return
        if appid not in self.games:
            return

        def done(count) -> None:
            if count is not None and appid in self.games:
                self.games[appid].players = int(count)
                self.refresh_table()
            for page in list(self.page_stack):
                if isValid(page) and page.appid == appid:
                    page.render()
            if self.player_task is task:
                self.player_task = None
            task.deleteLater()

        task = ApiTask(self.api.current_players, appid, parent=self)
        task.finished_result.connect(done)
        task.failed.connect(lambda message: self.status.showMessage(f"Онлайн: {message}"))
        self.player_task = task
        task.start()

    def toggle_track(self, appid: int) -> None:
        game = self.games.get(appid)
        if game is None:
            return
        if self.watchlist.get(appid):
            self.watchlist.remove(appid)
            self.status.showMessage(f"{game.name}: удалено из отслеживания.")
        else:
            self.watchlist.add(appid, game.name, game.image)
            self.status.showMessage(f"{game.name}: добавлено в отслеживание — уведомления о скидках включены.")
        self.refresh_table()

    # ------------------------------------------------------------ actions ---

    def _stop_workers(self) -> None:
        """Безопасная остановка всех фоновых задач (в том числе из трея)."""
        for name in (
            "worker", "watch_worker", "bulk_worker", "detail_task", "player_task"
        ):
            worker = getattr(self, name, None)
            if worker is None or not isValid(worker):
                setattr(self, name, None)
                continue
            still_running = False
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    # Блокирующий HTTP-запрос не реагирует на interruption,
                    # поэтому после короткого ожидания завершаем поток насильно:
                    # иначе Qt аварийно прервёт выход при уничтожении QThread.
                    if not worker.wait(1200):
                        worker.terminate()
                        worker.wait(1000)
                    still_running = worker.isRunning()
            except RuntimeError:
                pass
            # Удалять QThread, который ещё выполняется, нельзя — Qt аварийно
            # завершится. Оставляем объект родителю, поток сам остановится.
            if not still_running and isValid(worker):
                worker.deleteLater()
            setattr(self, name, None)
        self.detail_queue.clear()

    def open_settings(self) -> None:
        old_currency = self.settings.get("currency")
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        cc, lang = currency_region(self.settings.get("currency"))
        self.api.set_region(cc, lang)
        self._restart_timer()
        if self.settings.get("currency") == old_currency:
            self.status.showMessage("Настройки сохранены.")
            return

        # Цены хранятся в центах конкретной валюты — старые данные и база
        # уведомлений при смене валюты больше недействительны.
        self._stop_workers()
        self.store.clear_prices()
        self.detail_cache.clear()
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Обновить")
        for game in self.games.values():
            if game.section == "catalog":
                game.price = game.initial = None
                game.discount = 0
                game.formatted_price = game.formatted_initial = "—"
        for page in list(self.page_stack):
            if isValid(page):
                self.fetch_details(page.appid)
        self.refresh()
        self.status.showMessage("Валюта изменена — цены перезагружаются…")

    # -------------------------------------------------------------- tray ---

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
                self.images.clear()
                trim_memory()
            else:
                self._show_window()

    def _show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._force_quit = True
        self.tray.hide()
        self._stop_workers()
        self.close()
        # Когда окно скрыто в трее, закрытие окна не всегда завершает цикл
        # событий — выходим явно.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if (
            not self._force_quit
            and self.settings.get("minimize_to_tray")
            and self.tray.isVisible()
        ):
            event.ignore()
            self.hide()
            # В трее нет таблицы — кэш обложек не нужен, а весит десятки МБ.
            self.images.clear()
            trim_memory()
            self.tray.showMessage(
                "SteamSNM", "Приложение свернуто в трей.",
                QSystemTrayIcon.MessageIcon.Information, 3000,
            )
            return
        self.page_stack.clear()
        self.images.clear()
        self._stop_workers()
        self.store.close()
        event.accept()
