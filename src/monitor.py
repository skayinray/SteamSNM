"""Фоновый опрос Steam API и генерация событий/уведомлений."""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from notifications import notification_messages
from steam_api import FeaturedItem, GameInfo, SteamAPI, SteamAPIError
from storage import CatalogCache, Settings, Store, Watchlist


class MonitorWorker(QThread):
    progress = Signal(str, int, int)      # сообщение, выполнено, всего
    updated = Signal(list)                # список GameInfo
    events = Signal(list)                 # строки уведомлений
    releases = Signal(list)               # список FeaturedItem
    failed = Signal(str)
    succeeded = Signal(float)             # время завершения

    def __init__(
        self,
        api: SteamAPI,
        watchlist: Watchlist,
        store: Store,
        settings: Settings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.watchlist = watchlist
        self.store = store
        self.settings = settings

    def run(self) -> None:  # noqa: C901
        games: list[GameInfo] = []
        try:
            entries = self.watchlist.all()
            fetch_players = bool(self.settings.get("fetch_players"))
            show_releases = bool(self.settings.get("show_releases"))
            total = len(entries) + (1 if show_releases else 0)
            done = 0

            for entry in entries:
                if self.isInterruptionRequested():
                    return
                appid = int(entry["appid"])
                self.progress.emit(f"Загрузка: {entry.get('name') or appid}", done, total)
                game = self.api.app_details(appid)
                if game is None:
                    # Steam иногда hides specific titles for a region.
                    game = GameInfo(
                        appid=appid,
                        name=entry.get("name") or f"App {appid}",
                        image=entry.get("image", ""),
                        formatted_price="Нет данных для региона",
                        updated=int(time.time()),
                    )
                else:
                    if fetch_players:
                        game.players = self.api.current_players(appid)
                    if entry.get("image") and not game.image:
                        game.image = entry["image"]
                games.append(game)
                done += 1
                self.progress.emit(f"Загрузка: {entry.get('name') or appid}", done, total)
                time.sleep(0.12)

            messages: list[str] = []
            for game in games:
                if self.isInterruptionRequested():
                    return
                previous = self.store.watch_state(game.appid)
                entry = self.watchlist.get(game.appid) or {}
                messages.extend(self._compare(game, previous, entry))
                self.store.record_watch(game)

            if games:
                self.updated.emit(games)
            if messages:
                self.events.emit(messages)

            if show_releases:
                if self.isInterruptionRequested():
                    return
                self.progress.emit("Загрузка новинок Steam", done, total)
                featured = self.api.featured()
                fresh_ids = self.store.new_releases(i.appid for i in featured["new_releases"])
                self.store.mark_releases(i.appid for i in featured["new_releases"])
                fresh = [i for i in featured["new_releases"] if i.appid in fresh_ids]
                self.releases.emit(fresh)
                done += 1
                self.progress.emit("Загрузка новинок Steam", done, total)
                if fresh and self.settings.get("notify_releases"):
                    names = ", ".join(item.name for item in fresh[:3])
                    if len(fresh) > 3:
                        names += f" и ещё {len(fresh) - 3}"
                    self.events.emit([f"🆕 Новинки в Steam: {names}"])

            self.succeeded.emit(time.time())
        except (SteamAPIError, Exception) as exc:  # noqa: BLE001 - не роняем GUI
            if games:
                self.updated.emit(games)
            self.failed.emit(str(exc))

    def _compare(self, game: GameInfo, previous: dict | None, entry: dict) -> list[str]:
        return notification_messages(game, previous, entry, self.settings)


class CatalogWorker(QThread):
    """Загружает витрину Steam (скидки, новинки, топ, скоро)."""

    progress = Signal(str)
    updated = Signal(list)
    releases = Signal(list)
    events = Signal(list)
    failed = Signal(str)
    succeeded = Signal(float)

    def __init__(self, api: SteamAPI, store: Store, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.store = store
        self.settings = settings

    def run(self) -> None:
        try:
            games = self.api.catalog()
            if self.isInterruptionRequested():
                return
            self.progress.emit(f"Загружено игр: {len(games)}")
            for game in games:
                self.store.record(game)
            self.updated.emit(games)

            fresh_section = [g for g in games if g.section == "new_releases"]
            fresh_ids = self.store.new_releases(g.appid for g in fresh_section)
            self.store.mark_releases(g.appid for g in fresh_section)
            fresh = [g for g in fresh_section if g.appid in fresh_ids]
            self.releases.emit(fresh)
            if fresh and self.settings.get("notify_releases"):
                names = ", ".join(game.name for game in fresh[:3])
                if len(fresh) > 3:
                    names += f" и ещё {len(fresh) - 3}"
                self.events.emit([f"🆕 Новинки в Steam: {names}"])
            self.succeeded.emit(time.time())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class BulkCatalogWorker(QThread):
    """Постепенно загружает весь каталог Steam страницами по 100 игр."""

    progress = Signal(int, int)          # страниц, игр
    batch = Signal(list)                 # список GameInfo
    completed = Signal(int)              # всего игр
    failed = Signal(str)

    def __init__(self, api: SteamAPI, pages: int = 200, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.pages = pages

    def run(self) -> None:
        total = 0
        try:
            for page in range(self.pages):
                if self.isInterruptionRequested():
                    break
                results = self.api.bulk_page(page * 100)
                if not results:
                    break
                games = [
                    GameInfo(
                        appid=item.appid,
                        name=item.name,
                        image=item.image,
                        section="catalog",
                        updated=int(time.time()),
                    )
                    for item in results
                ]
                total += len(games)
                self.batch.emit(games)
                self.progress.emit(page + 1, total)
                time.sleep(0.08)
            self.completed.emit(total)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ApiTask(QThread):
    """Универсальная однозадачная обёртка над синхронным API (для поиска)."""

    finished_result = Signal(object)
    failed = Signal(str)

    def __init__(self, function, *args, parent=None) -> None:
        super().__init__(parent)
        self.function = function
        self.args = args

    def run(self) -> None:
        try:
            self.finished_result.emit(self.function(*self.args))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


__all__ = ["MonitorWorker", "CatalogWorker", "BulkCatalogWorker", "ApiTask", "FeaturedItem"]
