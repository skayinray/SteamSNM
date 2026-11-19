"""Локальное хранилище: настройки, список отслеживания, история цен."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from steam_api import GameInfo

APP_NAME = "SteamSNM"

BASE_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_NAME
BASE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS: dict[str, Any] = {
    "currency": "RUB",
    "cc": "ru",
    "lang": "russian",
    "interval_min": 15,
    "notify_discounts": True,
    "notify_price_drop": True,
    "notify_target": True,
    "notify_releases": True,
    "show_releases": True,
    "fetch_players": True,
    "minimize_to_tray": True,
    "autostart": False,
    "auto_refresh": True,
}


class Settings:
    def __init__(self) -> None:
        self.path = BASE_DIR / "settings.json"
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
        self.save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
        self.save()

    @property
    def interval_ms(self) -> int:
        return max(1, int(self.get("interval_min") or 15)) * 60_000


class Watchlist:
    """Список отслеживаемых игр (потокобезопасный)."""

    def __init__(self) -> None:
        self.path = BASE_DIR / "watchlist.json"
        self._lock = threading.RLock()
        self._entries: dict[int, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("games", []) if isinstance(data, dict) else data or []
            for item in items:
                try:
                    appid = int(item["appid"])
                    self._entries[appid] = {
                        "appid": appid,
                        "name": item.get("name", f"App {appid}"),
                        "image": item.get("image", ""),
                        "target_price": item.get("target_price"),
                        "added_at": item.get("added_at", int(time.time())),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            payload = {"games": list(self._entries.values())}
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries.values()]

    def appids(self) -> list[int]:
        with self._lock:
            return list(self._entries)

    def get(self, appid: int) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(appid)
            return dict(entry) if entry else None

    def add(self, appid: int, name: str, image: str = "") -> bool:
        with self._lock:
            if appid in self._entries:
                return False
            self._entries[appid] = {
                "appid": appid,
                "name": name,
                "image": image,
                "target_price": None,
                "added_at": int(time.time()),
            }
        self.save()
        return True

    def remove(self, appid: int) -> bool:
        with self._lock:
            existed = self._entries.pop(appid, None) is not None
        if existed:
            self.save()
        return existed

    def set_target(self, appid: int, target: int | None) -> None:
        with self._lock:
            entry = self._entries.get(appid)
            if entry is not None:
                entry["target_price"] = target
        self.save()

    def replace_all(self, entries: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self._lock:
            for item in entries:
                try:
                    appid = int(item["appid"])
                except (KeyError, TypeError, ValueError):
                    continue
                if appid not in self._entries:
                    count += 1
                self._entries[appid] = {
                    "appid": appid,
                    "name": item.get("name", f"App {appid}"),
                    "image": item.get("image", ""),
                    "target_price": item.get("target_price"),
                    "added_at": item.get("added_at", int(time.time())),
                }
        self.save()
        return count


class CatalogCache:
    """Кэш большого каталога Steam, чтобы не грузить его заново при старте."""

    def __init__(self, name: str = "catalog.json") -> None:
        self.path = BASE_DIR / name

    def load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("games", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save(self, games: Iterable[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"games": list(games)}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def age_days(self) -> float:
        if not self.path.exists():
            return float("inf")
        return (time.time() - self.path.stat().st_mtime) / 86400.0


class Store:
    """SQLite: история цен, последнее состояние, замеченные новинки."""

    def __init__(self) -> None:
        self.path = BASE_DIR / "history.sqlite3"
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    appid INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    price INTEGER,
                    initial INTEGER,
                    discount INTEGER,
                    PRIMARY KEY (appid, ts)
                );
                CREATE INDEX IF NOT EXISTS idx_history_appid_ts ON price_history(appid, ts);
                CREATE TABLE IF NOT EXISTS app_state (
                    appid INTEGER PRIMARY KEY,
                    name TEXT,
                    price INTEGER,
                    initial INTEGER,
                    discount INTEGER,
                    players INTEGER,
                    ts INTEGER
                );
                CREATE TABLE IF NOT EXISTS seen_releases (
                    appid INTEGER PRIMARY KEY,
                    ts INTEGER
                );
                CREATE TABLE IF NOT EXISTS watch_state (
                    appid INTEGER PRIMARY KEY,
                    price INTEGER,
                    initial INTEGER,
                    discount INTEGER,
                    ts INTEGER
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def record(self, game: GameInfo) -> None:
        ts = game.updated or int(time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO price_history(appid, ts, price, initial, discount) VALUES (?,?,?,?,?)",
                (game.appid, ts, game.price, game.initial, game.discount),
            )
            conn.execute(
                """INSERT OR REPLACE INTO app_state(appid, name, price, initial, discount, players, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (game.appid, game.name, game.price, game.initial, game.discount, game.players, ts),
            )

    def state(self, appid: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM app_state WHERE appid=?", (appid,)).fetchone()
        return dict(row) if row else None

    def watch_state(self, appid: int) -> dict[str, Any] | None:
        """Отдельный базовый уровень для уведомлений по отслеживаемым играм."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watch_state WHERE appid=?", (appid,)).fetchone()
        return dict(row) if row else None

    def record_watch(self, game: GameInfo) -> None:
        ts = game.updated or int(time.time())
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO watch_state(appid, price, initial, discount, ts)
                   VALUES (?,?,?,?,?)""",
                (game.appid, game.price, game.initial, game.discount, ts),
            )

    def series(self, appid: int, limit: int = 400) -> list[tuple[int, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, price FROM price_history WHERE appid=? AND price IS NOT NULL ORDER BY ts DESC LIMIT ?",
                (appid, limit),
            ).fetchall()
        return [(int(r["ts"]), int(r["price"])) for r in reversed(rows)]

    def stats(self, appid: int) -> dict[str, int | None]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT MIN(price) AS low, MAX(price) AS high, AVG(price) AS avg,
                          COUNT(*) AS n, MAX(ts) AS last_ts
                   FROM price_history WHERE appid=? AND price IS NOT NULL""",
                (appid,),
            ).fetchone()
        if not row:
            return {"low": None, "high": None, "avg": None, "n": 0, "last_ts": None}
        return {
            "low": row["low"], "high": row["high"],
            "avg": int(row["avg"]) if row["avg"] is not None else None,
            "n": int(row["n"]), "last_ts": row["last_ts"],
        }

    def new_releases(self, appids: Iterable[int]) -> list[int]:
        ids = [int(i) for i in appids]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT appid FROM seen_releases WHERE appid IN ({placeholders})", ids
            ).fetchall()
            seen = {int(r["appid"]) for r in rows}
        return [i for i in ids if i not in seen]

    def mark_releases(self, appids: Iterable[int]) -> None:
        ts = int(time.time())
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_releases(appid, ts) VALUES (?,?)",
                [(int(i), ts) for i in appids],
            )

    def remove_app(self, appid: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM app_state WHERE appid=?", (appid,))

    def clear_prices(self) -> None:
        """Сбросить цены при смене валюты: старые суммы в другой валюте несравнимы."""
        with self._connect() as conn:
            conn.execute("DELETE FROM price_history")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM app_state")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
