import gzip
import urllib.parse

import steam_api


def test_currency_region_mapping_and_fallback():
    assert steam_api.currency_region("usd") == ("us", "english")
    assert steam_api.currency_region("RUB") == ("ru", "russian")
    assert steam_api.currency_region("") == ("ru", "russian")
    assert steam_api.currency_region("UNKNOWN") == ("ru", "russian")
    assert len(steam_api.CURRENCY_REGIONS) == 14
    assert all(len(value) == 3 for value in steam_api.CURRENCY_REGIONS.values())


def test_format_money():
    assert steam_api.format_money(None) == "—"
    assert steam_api.format_money(100000, "USD") == "$1 000"
    assert steam_api.format_money(12345, "RUB") == "123,45 ₽"
    assert steam_api.format_money(199, "EUR") == "1,99 €"


def test_gzip_response_decoding():
    assert steam_api._decode(gzip.compress(b'{"ok": true}')) == '{"ok": true}'
    assert steam_api._decode(b'{"ok": true}') == '{"ok": true}'


def test_app_details_url_and_parsing(monkeypatch):
    urls = []

    def fake_get_json(url, **kwargs):
        urls.append(url)
        return {
            "42": {
                "success": True,
                "data": {
                    "name": "Sample Game",
                    "header_image": "https://cdn.example/42.jpg",
                    "is_free": False,
                    "price_overview": {
                        "final": 999,
                        "initial": 1999,
                        "currency": "RUB",
                        "discount_percent": 50,
                        "final_formatted": "999 ₽",
                    },
                    "release_date": {"date": "1 Jan 2026"},
                    "genres": [{"description": "Action"}, {"description": "RPG"}],
                    "recommendations": {"total_positive": 8, "total_reviews": 10},
                },
            }
        }

    monkeypatch.setattr(steam_api, "get_json", fake_get_json)
    api = steam_api.SteamAPI(cc="de", lang="english")
    game = api.app_details(42)

    assert len(urls) == 1
    parsed = urllib.parse.urlparse(urls[0])
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/api/appdetails"
    assert query["appids"] == ["42"]
    assert query["cc"] == ["de"]
    assert query["l"] == ["english"]
    assert query["filters"] == [steam_api.FILTERS]

    assert game is not None
    assert game.appid == 42
    assert game.name == "Sample Game"
    assert game.price == 999 and game.initial == 1999
    assert game.discount == 50 and game.currency == "RUB"
    assert game.formatted_price == "999 ₽"
    assert game.genres == ["Action", "RPG"]
    assert game.rating == "80% (10)"
    assert game.savings == 1000


def test_app_details_unsuccessful_response_is_none(monkeypatch):
    monkeypatch.setattr(steam_api, "get_json", lambda url, **kwargs: {"42": {"success": False}})
    assert steam_api.SteamAPI().app_details(42) is None


def test_search_filters_non_app_items_and_builds_url(monkeypatch):
    urls = []

    def fake_get_json(url, **kwargs):
        urls.append(url)
        return {
            "items": [
                {"type": "app", "id": 7, "name": "Co-op Game", "tiny_image": "a.jpg",
                 "price": {"final": 500, "currency": "RUB"}},
                {"type": "bundle", "id": 8, "name": "Not a game"},
            ]
        }

    monkeypatch.setattr(steam_api, "get_json", fake_get_json)
    results = steam_api.SteamAPI(cc="ru", lang="russian").search("co op", limit=5)

    assert len(results) == 1
    assert results[0].appid == 7 and results[0].price == 500
    query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
    assert query["term"] == ["co op"]
    assert query["cc"] == ["ru"]


def test_bulk_page_extracts_appids_from_logo(monkeypatch):
    urls = []

    def fake_get_json(url, **kwargs):
        urls.append(url)
        return {
            "items": [
                {"name": "A", "logo": "https://cdn.steamstatic.com/steam/apps/11/header.jpg"},
                {"name": "B", "logo": "https://example.org/no-app-id.jpg"},
            ]
        }

    monkeypatch.setattr(steam_api, "get_json", fake_get_json)
    results = steam_api.SteamAPI().bulk_page(200)

    assert [item.appid for item in results] == [11]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
    assert query["start"] == ["200"] and query["count"] == ["100"]


def test_current_players_parsing(monkeypatch):
    responses = [
        {"response": {"result": 1, "player_count": 1234}},
        {"response": {"result": 42, "player_count": 1234}},
        {"response": {}},
    ]
    monkeypatch.setattr(steam_api, "get_json", lambda url, **kwargs: responses.pop(0))
    assert steam_api.SteamAPI().current_players(9) == 1234
    assert steam_api.SteamAPI().current_players(9) is None
    assert steam_api.SteamAPI().current_players(9) is None


def test_featured_parsing_and_catalog_deduplication(monkeypatch):
    payload = {
        "specials": {"items": [
            {"id": 1, "name": "Sale", "type": 0, "final_price": 100,
             "original_price": 200, "discount_percent": 50, "currency": "RUB"},
            {"id": 2, "name": "Bundle", "type": 1},
        ]},
        "new_releases": {"items": [
            {"id": 1, "name": "Duplicate", "type": 0, "final_price": 100},
            {"id": 3, "name": "New", "type": 0, "final_price": 300},
        ]},
        "top_sellers": {"items": []},
        "coming_soon": {"items": []},
    }
    monkeypatch.setattr(steam_api, "get_json", lambda url, **kwargs: payload)
    api = steam_api.SteamAPI()
    featured = api.featured()

    assert [item.appid for item in featured["specials"]] == [1]
    assert [item.appid for item in featured["new_releases"]] == [1, 3]

    # Catalog also calls search(); return no items so featured data is enough.
    monkeypatch.setattr(steam_api, "get_json", lambda url, **kwargs: (
        payload if "featuredcategories" in url else {"items": []}
    ))
    games = api.catalog()
    assert [game.appid for game in games] == [1, 3]
    assert games[0].section == "specials"
