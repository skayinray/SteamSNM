import json

import pytest

import storage
from steam_api import GameInfo


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "BASE_DIR", tmp_path)
    return tmp_path


def make_game(appid, price, ts, *, initial=None, discount=0, name="Game"):
    return GameInfo(
        appid=appid,
        name=name,
        price=price,
        initial=initial if initial is not None else price,
        discount=discount,
        formatted_price="price",
        currency="RUB",
        updated=ts,
    )


def test_settings_defaults_and_persistence(isolated):
    first = storage.Settings()
    assert first.get("currency") == "RUB"
    assert first.interval_ms == 15 * 60_000
    first.set("currency", "USD")
    first.set("interval_min", -1)

    second = storage.Settings()
    assert second.get("currency") == "USD"
    assert second.interval_ms == 60_000  # clamped to one minute
    stored = json.loads((isolated / "settings.json").read_text(encoding="utf-8"))
    assert stored["currency"] == "USD"


def test_watchlist_add_target_and_remove(isolated):
    watchlist = storage.Watchlist()
    assert watchlist.add(10, "Ten", "ten.jpg") is True
    assert watchlist.add(10, "Ten") is False
    assert watchlist.appids() == [10]
    assert watchlist.get(10)["name"] == "Ten"

    watchlist.set_target(10, 500)
    assert watchlist.get(10)["target_price"] == 500

    reloaded = storage.Watchlist()
    assert reloaded.get(10)["target_price"] == 500
    assert reloaded.remove(10) is True
    assert reloaded.remove(10) is False
    assert storage.Watchlist().all() == []


def test_watchlist_replace_all_returns_new_count(isolated):
    watchlist = storage.Watchlist()
    watchlist.add(1, "One")
    added = watchlist.replace_all([
        {"appid": 1, "name": "One updated"},
        {"appid": 2, "name": "Two"},
        {"appid": "bad"},
    ])
    assert added == 1
    assert set(watchlist.appids()) == {1, 2}
    assert watchlist.get(1)["name"] == "One updated"


def test_catalog_cache_roundtrip_and_age(isolated):
    cache = storage.CatalogCache("catalog_global.json")
    assert cache.load() == []
    assert cache.age_days() == float("inf")

    cache.save([{"appid": 1, "name": "Game"}])
    assert cache.path.name == "catalog_global.json"
    assert storage.CatalogCache("catalog_global.json").load() == [
        {"appid": 1, "name": "Game"}
    ]
    assert cache.age_days() < 1


@pytest.fixture
def store(isolated):
    instance = storage.Store()
    yield instance
    instance.close()


def test_store_records_history_and_stats(store):
    store.record(make_game(5, 1000, 100, initial=2000, discount=50))
    store.record(make_game(5, 800, 200, initial=2000, discount=60))
    store.record(make_game(5, None, 300))

    assert store.series(5) == [(100, 1000), (200, 800)]
    assert store.stats(5) == {"low": 800, "high": 1000, "avg": 900, "n": 2, "last_ts": 200}
    assert store.state(5)["price"] is None
    assert store.series(999) == []
    assert store.stats(999)["n"] == 0


def test_store_watch_state_is_independent(store):
    store.record(make_game(6, 1000, 100))
    store.record_watch(make_game(6, 700, 100))
    assert store.state(6)["price"] == 1000
    assert store.watch_state(6)["price"] == 700


def test_clear_prices_removes_all_currency_data(store):
    store.record(make_game(7, 1000, 100))
    store.record_watch(make_game(7, 1000, 100))
    store.mark_releases([7])
    store.clear_prices()

    assert store.stats(7)["n"] == 0
    assert store.watch_state(7) is None
    assert store.state(7) is None
    assert store.new_releases([7]) == []  # seen_releases is not price data


def test_new_releases_are_only_reported_once(store):
    assert store.new_releases([1, 2]) == [1, 2]
    store.mark_releases([1, 2])
    assert store.new_releases([1, 2, 3]) == [3]


def test_remove_app_only_deletes_state(store):
    store.record(make_game(8, 500, 100))
    store.remove_app(8)
    assert store.state(8) is None
    assert store.series(8) == [(100, 500)]
