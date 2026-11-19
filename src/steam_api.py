"""Клиент официальных (публичных) endpoint-ов Steam.

SteamDB не предоставляет публичного API, поэтому SteamSNM получает данные
напрямую из Steam Store API и Steam Web API. Никакого парсинга сайта SteamDB.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_AGENT = "SteamSNM/1.0 (desktop client; Steam Store API)"

# В AppImage системные CA-сертификаты не видны bundled Python, поэтому он
# падает с CERTIFICATE_VERIFY_FAILED. Бандл кладётся в образ сборщиком.
_CA_CANDIDATES = (
    "$APPDIR/usr/share/steamsnm/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)


_SSL_CONTEXT: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext | None:
    """SSL-контекст с CA-бандлом, доступным и в AppImage, и в системе."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    for pattern in _CA_CANDIDATES:
        path = Path(os.path.expandvars(pattern))
        if path.is_file():
            try:
                _SSL_CONTEXT = ssl.create_default_context(cafile=str(path))
                return _SSL_CONTEXT
            except (OSError, ssl.SSLError):
                continue
    return None

FILTERS = "basic,price_overview,release_date,genres,recommendations"

# Широкие запросы для расширения витрины за пределы featured-подборок.
POPULAR_TERMS = (
    "action", "adventure", "rpg", "strategy", "simulation", "racing",
    "puzzle", "horror", "survival", "space", "city", "war", "sports",
)

CURRENCY_SYMBOLS = {
    "RUB": "₽", "USD": "$", "EUR": "€", "GBP": "£", "UAH": "₴",
    "TRY": "₺", "PLN": "zł", "CNY": "¥", "JPY": "¥", "KRW": "₩",
    "BRL": "R$", "INR": "₹", "ARS": "$", "CLP": "$", "MXN": "$",
    "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "ILS": "₪", "AED": "د.إ", "SAR": "﷼", "ZAR": "R", "SEK": "kr",
    "NOK": "kr", "DKK": "kr", "CZK": "Kč", "HUF": "Ft", "RON": "lei",
    "THB": "฿", "IDR": "Rp", "PHP": "₱", "MYR": "RM", "VND": "₫",
}

# Валюта определяет регион (cc) запроса к Steam Store: Steam возвращает цены
# именно в валюте указанного региона. По умолчанию — рубли.
CURRENCY_REGIONS: dict[str, tuple[str, str, str]] = {
    "RUB": ("ru", "russian", "Российский рубль (₽)"),
    "USD": ("us", "english", "Доллар США ($)"),
    "EUR": ("de", "english", "Евро (€)"),
    "GBP": ("uk", "english", "Фунт стерлингов (£)"),
    "UAH": ("ua", "russian", "Украинская гривна (₴)"),
    "TRY": ("tr", "english", "Турецкая лира (₺)"),
    "PLN": ("pl", "english", "Польский злотый (zł)"),
    "CNY": ("cn", "schinese", "Китайский юань (¥)"),
    "JPY": ("jp", "japanese", "Японская йена (¥)"),
    "KRW": ("kr", "koreana", "Южнокорейский вон (₩)"),
    "BRL": ("br", "brazilian", "Бразильский реал (R$)"),
    "INR": ("in", "english", "Индийская рупия (₹)"),
    "AUD": ("au", "english", "Австралийский доллар (A$)"),
    "CAD": ("ca", "english", "Канадский доллар (C$)"),
}


def currency_region(code: str) -> tuple[str, str]:
    """Регион и язык Steam для выбранной валюты (по умолчанию RUB/ru)."""
    cc, lang, _label = CURRENCY_REGIONS.get(
        (code or "").upper(), CURRENCY_REGIONS["RUB"]
    )
    return cc, lang


class SteamAPIError(RuntimeError):
    """Ошибка запроса к Steam API."""


def format_money(cents: int | None, currency: str = "USD") -> str:
    """Форматирует цену в центах в человекочитаемый вид."""
    if cents is None:
        return "—"
    value = cents / 100.0
    if abs(value - round(value)) < 1e-9:
        number = f"{int(round(value)):,}".replace(",", " ")
    else:
        number = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency)
    if currency.upper() in ("USD", "AUD", "CAD", "NZD", "BRL", "MXN", "ARS", "CLP"):
        return f"{symbol}{number}"
    return f"{number} {symbol}"


def _decode(body: bytes) -> str:
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    return body.decode("utf-8", errors="replace")


def get_json(url: str, timeout: int = 20, retries: int = 2) -> Any:
    """GET + JSON с повторами при 429/5xx и сетевых ошибках."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, */*",
                    "Accept-Language": "en",
                },
            )
            context = _ssl_context()
            kwargs = {"context": context} if context is not None else {}
            with urllib.request.urlopen(request, timeout=timeout, **kwargs) as response:
                payload = _decode(response.read())
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                # Steam иногда отвечает `null` при временном сбое.
                raise SteamAPIError(f"Некорректный ответ Steam API: {payload[:60]!r}") from None
        except urllib.error.HTTPError as exc:
            last_error = SteamAPIError(f"HTTP {exc.code}: {exc.reason}")
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_error from None
        except (urllib.error.URLError, TimeoutError, OSError, SteamAPIError) as exc:
            last_error = exc if isinstance(exc, SteamAPIError) else SteamAPIError(str(exc))
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_error from None
    raise last_error or SteamAPIError("Неизвестная ошибка запроса")


@dataclass
class GameInfo:
    appid: int
    name: str
    image: str = ""
    price: int | None = None
    initial: int | None = None
    currency: str = "USD"
    discount: int = 0
    formatted_price: str = "—"
    formatted_initial: str = "—"
    players: int | None = None
    release_date: str = ""
    genres: list[str] = field(default_factory=list)
    positive: int = 0
    total_reviews: int = 0
    free: bool = False
    updated: int = 0
    section: str = ""

    @property
    def savings(self) -> int:
        if self.price is None or self.initial is None:
            return 0
        return max(0, self.initial - self.price)

    @property
    def rating(self) -> str:
        if not self.total_reviews:
            return "—"
        percent = round(100 * self.positive / self.total_reviews)
        return f"{percent}% ({self.total_reviews:,})".replace(",", " ")


@dataclass
class SearchResult:
    appid: int
    name: str
    image: str = ""
    price: int | None = None
    currency: str = "USD"


@dataclass
class FeaturedItem:
    appid: int
    name: str
    image: str = ""
    price: int | None = None
    initial: int | None = None
    discount: int = 0
    currency: str = "RUB"


class SteamAPI:
    def __init__(self, cc: str = "ru", lang: str = "russian") -> None:
        self.cc = cc
        self.lang = lang
        self._cache: dict[str, tuple[float, Any]] = {}

    def set_region(self, cc: str, lang: str) -> None:
        if (cc, lang) != (self.cc, self.lang):
            self.cc, self.lang = cc, lang
            self._cache.clear()

    def _cached(self, key: str, ttl: float, producer) -> Any:
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = producer()
        self._cache[key] = (now, value)
        return value

    def app_details(self, appid: int) -> GameInfo | None:
        """Один appdetails-запрос (Steam больше не принимает пачки appids)."""
        url = (
            "https://store.steampowered.com/api/appdetails?"
            f"appids={appid}&cc={urllib.parse.quote(self.cc)}"
            f"&l={urllib.parse.quote(self.lang)}&filters={FILTERS}"
        )
        payload = get_json(url)
        entry = payload.get(str(appid)) if isinstance(payload, dict) else None
        if not entry or not entry.get("success"):
            return None
        return self.parse_details(appid, entry.get("data") or {})

    def parse_details(self, appid: int, data: dict[str, Any]) -> GameInfo | None:
        """Преобразовать данные appdetails в GameInfo (используется и для страницы игры)."""
        if not data:
            return None
        price_data = data.get("price_overview") or {}
        free = bool(data.get("is_free"))
        price = price_data.get("final")
        initial = price_data.get("initial")
        currency = price_data.get("currency") or "USD"
        if free and price is None:
            price, initial = 0, 0

        genres = [g.get("description", "") for g in data.get("genres", []) if g.get("description")]
        recommendations = data.get("recommendations") or {}

        return GameInfo(
            appid=appid,
            name=data.get("name") or f"App {appid}",
            image=data.get("header_image") or data.get("capsule_image") or "",
            price=price,
            initial=initial,
            currency=currency,
            discount=int(price_data.get("discount_percent") or 0),
            formatted_price=price_data.get("final_formatted") or (format_money(price, currency) if price is not None else "—"),
            formatted_initial=price_data.get("initial_formatted") or (format_money(initial, currency) if initial is not None else "—"),
            players=None,
            release_date=(data.get("release_date") or {}).get("date", ""),
            genres=genres[:5],
            positive=int(recommendations.get("total_positive") or 0),
            total_reviews=int(recommendations.get("total_reviews") or 0),
            free=free,
            updated=int(time.time()),
        )

    def search(self, term: str, limit: int = 20) -> list[SearchResult]:
        term = term.strip()
        if len(term) < 2:
            return []
        key = f"search:{self.cc}:{self.lang}:{term.lower()}"
        raw = self._cached(key, 120.0, lambda: get_json(
            "https://store.steampowered.com/api/storesearch/?"
            f"term={urllib.parse.quote(term)}&l={urllib.parse.quote(self.lang)}"
            f"&cc={urllib.parse.quote(self.cc)}"
        ))
        results: list[SearchResult] = []
        for item in (raw or {}).get("items", [])[:limit]:
            if item.get("type") != "app":
                continue
            price = item.get("price") or {}
            results.append(SearchResult(
                appid=int(item["id"]),
                name=item.get("name", ""),
                image=item.get("tiny_image", ""),
                price=price.get("final"),
                currency=price.get("currency", "USD"),
            ))
        return results

    def bulk_page(self, start: int) -> list[SearchResult]:
        """Страница каталога Steam (до 100 игр) с обложками."""
        url = (
            "https://store.steampowered.com/search/results/?query"
            f"&start={int(start)}&count=100&json=1"
            f"&cc={urllib.parse.quote(self.cc)}&l={urllib.parse.quote(self.lang)}"
        )
        raw = get_json(url)
        results: list[SearchResult] = []
        for item in (raw or {}).get("items", []):
            logo = item.get("logo") or ""
            match = re.search(r"steam/apps/(\d+)/", logo)
            if not match:
                continue
            results.append(SearchResult(
                appid=int(match.group(1)),
                name=item.get("name", ""),
                image=logo,
            ))
        return results

    def app_details_full(self, appid: int) -> dict[str, Any]:
        """Полные данные игры для страницы (описание, скриншоты и т.д.)."""
        url = (
            "https://store.steampowered.com/api/appdetails?"
            f"appids={appid}&cc={urllib.parse.quote(self.cc)}"
            f"&l={urllib.parse.quote(self.lang)}"
        )
        payload = get_json(url)
        entry = payload.get(str(appid)) if isinstance(payload, dict) else None
        if not entry or not entry.get("success"):
            return {}
        return entry.get("data") or {}

    def current_players(self, appid: int) -> int | None:
        key = f"players:{appid}"
        raw = self._cached(key, 45.0, lambda: get_json(
            f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={appid}"
        ))
        try:
            response = raw["response"]
            if int(response.get("result", 0)) != 1:
                return None
            return int(response.get("player_count", 0))
        except (KeyError, TypeError, ValueError):
            return None

    def featured(self) -> dict[str, list[FeaturedItem]]:
        key = f"featured:{self.cc}:{self.lang}"
        raw = self._cached(key, 180.0, lambda: get_json(
            "https://store.steampowered.com/api/featuredcategories/?"
            f"cc={urllib.parse.quote(self.cc)}&l={urllib.parse.quote(self.lang)}"
        ))

        def convert(section: str) -> list[FeaturedItem]:
            items = []
            for item in ((raw or {}).get(section) or {}).get("items", []):
                if int(item.get("type", -1)) != 0 or not item.get("id"):
                    continue
                items.append(FeaturedItem(
                    appid=int(item["id"]),
                    name=item.get("name", ""),
                    image=item.get("large_capsule_image") or item.get("small_capsule_image") or "",
                    price=item.get("final_price"),
                    initial=item.get("original_price"),
                    discount=int(item.get("discount_percent") or 0),
                    currency=item.get("currency", self.cc.upper()),
                ))
            return items

        return {
            "specials": convert("specials"),
            "new_releases": convert("new_releases"),
            "top_sellers": convert("top_sellers"),
            "coming_soon": convert("coming_soon"),
        }

    def catalog(self) -> list[GameInfo]:
        """Витрина игр Steam: один запрос, цены и скидки уже включены."""
        games: list[GameInfo] = []
        seen: set[int] = set()
        for section, items in self.featured().items():
            for item in items:
                if item.appid in seen:
                    continue
                seen.add(item.appid)
                currency = item.currency
                games.append(GameInfo(
                    appid=item.appid,
                    name=item.name,
                    image=item.image,
                    price=item.price,
                    initial=item.initial,
                    currency=currency,
                    discount=item.discount,
                    formatted_price=format_money(item.price, currency),
                    formatted_initial=format_money(item.initial, currency),
                    free=(item.price == 0),
                    section=section,
                    updated=int(time.time()),
                ))

        for term in POPULAR_TERMS:
            try:
                results = self.search(term, limit=10)
            except SteamAPIError:
                continue
            for result in results:
                if result.appid in seen:
                    continue
                seen.add(result.appid)
                games.append(GameInfo(
                    appid=result.appid,
                    name=result.name,
                    image=result.image,
                    price=result.price,
                    initial=result.price,
                    currency=result.currency,
                    formatted_price=format_money(result.price, result.currency),
                    formatted_initial=format_money(result.price, result.currency),
                    section="popular",
                    updated=int(time.time()),
                ))
        return games
