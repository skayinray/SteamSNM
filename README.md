# SteamSNM

SteamSNM is a desktop Steam price monitor for Linux. It is distributed as an AppImage and built with PySide6 (Qt).

SteamSNM is an independent project. It is not a Valve or SteamDB product and does not scrape SteamDB. All data comes from public Steam Store API and Steam Web API endpoints.

## Features

- Loads the full global Steam catalog in the background, 100 games per page, and caches it in `catalog_global.json`. No manual add step is needed.
- Keeps games that are unavailable in your region in the list and marks them as such.
- Shows cover images and an in-window game page with description, genres, release date, developer, price, discount, current players, review score, and screenshots. Pages stack with a back button.
- Tracks games and sends system-tray notifications for new or larger discounts, price drops, target prices, free transitions, and new releases.
- Stores local price history with a chart. Changing the currency clears the history so values in different currencies are never mixed.
- Offers currency selection (RUB by default, 14 supported), configurable poll interval, optional autostart, tray minimize, search, filters, sorting, an event log, a dark Steam-like theme, and a single-instance lock.

## Run

```bash
chmod +x SteamSNM-x86_64.AppImage
./SteamSNM-x86_64.AppImage
```

If FUSE is unavailable:

```bash
./SteamSNM-x86_64.AppImage --appimage-extract-and-run
```

Settings and data live in `~/.config/SteamSNM`:

- `settings.json`, settings;
- `watchlist.json`, tracked games;
- `catalog_global.json`, cached global Steam catalog;
- `history.sqlite3`, price history, last known states, and seen releases.

## Verify a release

Every release asset is published with a `SHA256SUMS` file and an Ed25519 signature file (`<asset>.sig`). See [SECURITY.md](SECURITY.md) for the verification command and the embedded public key. Do not use an asset that fails verification.

## Build from source (Linux)

Requirements: Python 3.10+, PySide6 (see `requirements.txt`), `appimagetool`, Linux x86_64.

```bash
python3 -m pip install -r requirements.txt
curl -fL -o /tmp/appimagetool.AppImage \
  https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
echo "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0  /tmp/appimagetool.AppImage" | sha256sum -c -
chmod +x /tmp/appimagetool.AppImage
./build_appimage.sh /tmp/appimagetool.AppImage
```

`tools/build_appimage.py` bundles the Python interpreter, a pruned standard library, only the PySide6 modules the app imports, their recursive Qt dependencies, the required Qt plugins, one libpython, one OpenSSL generation, and the CA bundle. It then runs `appimagetool`. The result is `SteamSNM-x86_64.AppImage`. HTTPS verification is unchanged: the CA bundle is included and exported as `SSL_CERT_FILE` by `AppRun`.

To run directly from the source tree:

```bash
python3 src/main.py
```

## Windows

`SteamSNM.spec` is a PyInstaller specification. The release workflow pins the PyInstaller version and excludes unused PySide6 addons. Release assets are signed with the same Ed25519 key as the Linux build.

## macOS (manual only)

There is no macOS CI build and no signed or notarized macOS release. To run from source:

```bash
python3 -m pip install -r requirements.txt
python3 src/main.py
```

Packaging with PyInstaller on macOS is a manual step. Without an Apple Developer certificate and notarization, such a build stays unsigned and Gatekeeper will block or warn about it. This project does not claim otherwise.

## Tests

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q
```

CI runs the tests on Ubuntu and Windows for pushes and pull requests. The tests cover pure API, storage, and notification logic; they do not require network access or a display.

## APIs used

| Purpose | Endpoint |
| --- | --- |
| Game info and price | `store.steampowered.com/api/appdetails` |
| Game search | `store.steampowered.com/api/storesearch` |
| Current players | `api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1` |
| Featured categories | `store.steampowered.com/api/featuredcategories` |
| Full catalog pages | `store.steampowered.com/search/results` |

Steam accepts one `appid` per `appdetails` request, so polling is sequential with a small delay. Some games may have no data for the selected region.

## Project layout

```
src/
  main.py               entry point, single-instance lock
  steam_api.py          public Steam API client
  storage.py            JSON settings/watchlist/cache and SQLite history
  notifications.py      pure notification-trigger logic
  monitor.py            background workers
  ui/                   Qt interface
  resources/            icon and AppStream metadata
tools/
  build_appimage.py     slim AppImage builder
  sign_release.py       release signing and verification
tests/                  pytest suite (no Qt or network)
```

## License

MIT. See [LICENSE](LICENSE).

## Author

skayinray, <https://github.com/skayinray>

Source: <https://github.com/skayinray/SteamSNM>
