"""Build a slim SteamSNM-x86_64.AppImage from the local environment.

Usage:
    python3 tools/build_appimage.py [path/to/appimagetool.AppImage]

Only the PySide6 modules used by the application are bundled, together with
their recursive Qt dependencies, a small set of Qt plugins, one libpython
copy, one OpenSSL generation, and a pruned Python standard library. TLS
verification is unchanged: the CA bundle is included and exported by AppRun.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import sysconfig
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
APPDIR = BUILD / "AppDir"
OUTPUT = ROOT / "SteamSNM-x86_64.AppImage"

PY_VERSION = f"python{sys.version_info.major}.{sys.version_info.minor}"
STDLIB = Path(sysconfig.get_paths()["stdlib"])
SITE_PACKAGES = Path(sysconfig.get_paths()["purelib"])

# The app imports exactly these Qt bindings (QtSvg is not used).
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtCharts")

# Plugin stems needed for X11/XWayland, headless testing, game cover images,
# local PNG/ICO resources, and Qt Network TLS backends. Names vary between
# system Qt and the PySide6 wheel (with or without a "lib" prefix), so match
# by stem instead of copying whole plugin directories.
PLUGIN_STEMS = {
    "platforms": ("qxcb", "qminimal", "qoffscreen"),
    "imageformats": ("qjpeg", "qpng", "qico"),
    "tls": ("qopensslbackend", "qcertonlybackend"),
}

TLS_SEARCH_DIRS = (
    Path("/usr/lib"),
    Path("/usr/lib64"),
    Path("/usr/lib/x86_64-linux-gnu"),
    Path("/lib/x86_64-linux-gnu"),
)

# Prefer OpenSSL 3: it is used by Python's _ssl on mainstream build hosts and
# by the manylinux PySide6 wheel. Bundling both generations is unnecessary.
OPENSSL_VERSIONS = ("3", "4")

STDLIB_EXCLUDES = (
    "__pycache__",
    "*.pyc",
    "site-packages",
    "test",
    "idlelib",
    "tkinter",
    "lib2to3",
    "ensurepip",
    "turtledemo",
    "distutils",
    "venv",
    "pydoc_data",
    "config-*",
    "libpython*.a",
)


def qt_plugins_dir() -> Path:
    """Locate the Qt plugin directory that matches the installed PySide6."""
    candidates = (
        SITE_PACKAGES / "PySide6" / "Qt6" / "plugins",
        SITE_PACKAGES / "PySide6" / "Qt" / "plugins",
        Path("/usr/lib/qt6/plugins"),
    )
    for candidate in candidates:
        if (candidate / "platforms").is_dir():
            return candidate
    return candidates[-1]


def ignored_stdlib(directory: str, names: list[str]) -> set[str]:
    del directory
    return {
        name
        for name in names
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in STDLIB_EXCLUDES)
    }


def ldd_dependencies(path: Path) -> list[tuple[str, Path]]:
    """Return (soname, resolved path) for shared libraries reported by ldd.

    The soname reported by ldd must be kept when copying: it is the name the
    dynamic loader asks for, while the resolved file often has a full version
    suffix (libQt6Core.so.6 -> libQt6Core.so.6.12.0).
    """
    try:
        result = subprocess.run(
            ["ldd", str(path)], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    resolved: list[tuple[str, Path]] = []
    for line in result.stdout.splitlines():
        if "=>" in line:
            target = line.split("=>", 1)[1].strip().split()[0]
        else:
            parts = line.strip().split()
            target = parts[0] if parts else ""
        if target.startswith("/") and Path(target).is_file():
            resolved.append((Path(target).name, Path(target).resolve()))
    return resolved


def collect_qt_libs(targets: list[Path]) -> list[tuple[str, Path]]:
    """Follow ldd recursively from bindings and plugins; keep Qt/shiboken only."""
    found: dict[str, Path] = {}
    queue: deque[Path] = deque(targets)
    seen: set[Path] = set()
    while queue:
        target = queue.popleft()
        resolved = target.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        for soname, dependency in ldd_dependencies(resolved):
            if soname.startswith(("libQt6", "libshiboken6")):
                if soname not in found:
                    found[soname] = dependency
                    queue.append(dependency)
    return [(name, found[name]) for name in sorted(found)]


def select_plugins() -> list[Path]:
    base = qt_plugins_dir()
    selected: list[Path] = []
    for group, stems in PLUGIN_STEMS.items():
        directory = base / group
        if not directory.is_dir():
            continue
        for stem in stems:
            matches = sorted(directory.glob(f"*{stem}.so"))
            if matches:
                selected.append(matches[0])
            else:
                print(f"warning: Qt plugin {stem} not found in {directory}", file=sys.stderr)
    return selected


def collect_tls_libs() -> list[tuple[str, Path]]:
    """Bundle exactly one OpenSSL generation (libssl + matching libcrypto)."""
    for version in OPENSSL_VERSIONS:
        pair: list[tuple[str, Path]] = []
        for directory in TLS_SEARCH_DIRS:
            ssl = directory / f"libssl.so.{version}"
            crypto = directory / f"libcrypto.so.{version}"
            if ssl.is_file() and crypto.is_file():
                pair = [(ssl.name, ssl.resolve()), (crypto.name, crypto.resolve())]
                break
        if pair:
            return pair
    print("warning: no system OpenSSL library found", file=sys.stderr)
    return []


def copy_libpython(destination: Path) -> None:
    """Copy the shared libpython once, under the name the interpreter needs."""
    libdir = Path(sysconfig.get_config_var("LIBDIR") or "")
    soname = sysconfig.get_config_var("INSTSONAME")
    if soname and (libdir / str(soname)).is_file():
        source = (libdir / str(soname)).resolve()
        shutil.copy2(source, destination / source.name)
        return
    library = sysconfig.get_config_var("LDLIBRARY")
    if library and (libdir / str(library)).is_file():
        source = (libdir / str(library)).resolve()
        shutil.copy2(source, destination / source.name)
        if source.name != str(library):
            (destination / str(library)).symlink_to(source.name)


def main() -> int:
    tool = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("/tmp/appimagetool.AppImage")
    if not tool.exists():
        print(f"appimagetool not found: {tool}", file=sys.stderr)
        return 1

    if APPDIR.exists():
        shutil.rmtree(APPDIR)
    (APPDIR / "usr/bin").mkdir(parents=True)
    (APPDIR / "usr/lib").mkdir(parents=True)
    (APPDIR / "usr/share/steamsnm/src").mkdir(parents=True)

    print("[1/6] Python interpreter and pruned stdlib")
    executable = Path(sys.executable).resolve()
    shutil.copy2(executable, APPDIR / "usr/bin" / executable.name)
    (APPDIR / "usr/bin/python3").symlink_to(executable.name)
    copy_libpython(APPDIR / "usr/lib")
    shutil.copytree(
        STDLIB, APPDIR / "usr/lib" / PY_VERSION,
        ignore=ignored_stdlib, symlinks=True,
    )

    print("[2/6] PySide6 bindings actually used")
    site_dst = APPDIR / "usr/lib" / PY_VERSION / "site-packages"
    site_dst.mkdir(parents=True, exist_ok=True)
    pyside_src = SITE_PACKAGES / "PySide6"
    pyside_dst = site_dst / "PySide6"
    pyside_dst.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "_config.py"):
        source = pyside_src / name
        if source.is_file():
            shutil.copy2(source, pyside_dst / name)
    for name in QT_MODULES:
        matches = sorted(pyside_src.glob(f"{name}.*so"))
        if not matches:
            print(f"warning: PySide6 module {name} not found in {pyside_src}", file=sys.stderr)
        for source in matches:
            shutil.copy2(source.resolve(), pyside_dst / source.name)
    shiboken_src = SITE_PACKAGES / "shiboken6"
    if shiboken_src.is_dir():
        shutil.copytree(
            shiboken_src, site_dst / "shiboken6",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyi"),
            symlinks=True,
        )

    print("[3/6] Qt libraries and required plugins")
    bindings = [pyside_dst / source.name for name in QT_MODULES
                for source in sorted(pyside_src.glob(f"{name}.*so"))]
    plugins = select_plugins()
    qt_libs = collect_qt_libs(bindings + plugins)
    for soname, library in qt_libs + collect_tls_libs():
        shutil.copy2(library, APPDIR / "usr/lib" / soname)
    for plugin in plugins:
        relative = plugin.relative_to(qt_plugins_dir())
        target = APPDIR / "usr/lib/qt/plugins" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin.resolve(), target)

    print("[4/6] Application, icon, metadata, and CA bundle")
    shutil.copytree(
        ROOT / "src", APPDIR / "usr/share/steamsnm/src",
        ignore=shutil.ignore_patterns("__pycache__"),
        dirs_exist_ok=True,
    )
    # Bundled Python cannot see host CA paths inside the AppImage; AppRun
    # exports this bundle as SSL_CERT_FILE. Keep HTTPS verification enabled.
    ca_bundle = APPDIR / "usr/share/steamsnm/ca-bundle.crt"
    for pattern in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
    ):
        source = Path(pattern)
        if source.is_file():
            shutil.copy2(source.resolve(), ca_bundle)
            break

    icon = ROOT / "src/resources/icon.png"
    icon_dst = APPDIR / "usr/share/icons/hicolor/256x256/apps/steamsnm.png"
    icon_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon, icon_dst)
    shutil.copy2(icon, APPDIR / "steamsnm.png")
    (APPDIR / ".DirIcon").symlink_to("steamsnm.png")
    metainfo_dst = APPDIR / "usr/share/metainfo/SteamSNM.appdata.xml"
    metainfo_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src/resources/SteamSNM.appdata.xml", metainfo_dst)
    license_dst = APPDIR / "usr/share/doc/steamsnm/copyright"
    license_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", license_dst)

    desktop = """[Desktop Entry]
Type=Application
Name=SteamSNM
GenericName=Steam Monitor
Comment=Monitor Steam prices, discounts, and player counts
Exec=SteamSNM
Icon=steamsnm
Terminal=false
Categories=Network;Utility;
StartupNotify=true
"""
    (APPDIR / "SteamSNM.desktop").write_text(desktop, encoding="utf-8")
    applications = APPDIR / "usr/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "SteamSNM.desktop").write_text(desktop, encoding="utf-8")

    apprun = f"""#!/bin/sh
APPDIR="$(dirname "$(readlink -f "$0")")"
export APPDIR
export LD_LIBRARY_PATH="$APPDIR/usr/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
export QT_PLUGIN_PATH="$APPDIR/usr/lib/qt/plugins"
export QT_QPA_PLATFORM_PLUGIN_PATH="$APPDIR/usr/lib/qt/plugins/platforms"
export PYTHONPATH="$APPDIR/usr/lib/{PY_VERSION}/site-packages"
export XDG_DATA_DIRS="$APPDIR/usr/share:${{XDG_DATA_DIRS:-/usr/local/share:/usr/share}}"
# Qt performs many small allocations; limiting arenas keeps background RSS low.
export MALLOC_ARENA_MAX=2
if [ -f "$APPDIR/usr/share/steamsnm/ca-bundle.crt" ]; then
    export SSL_CERT_FILE="$APPDIR/usr/share/steamsnm/ca-bundle.crt"
fi
exec "$APPDIR/usr/bin/{executable.name}" "$APPDIR/usr/share/steamsnm/src/main.py" "$@"
"""
    apprun_path = APPDIR / "AppRun"
    apprun_path.write_text(apprun, encoding="utf-8")
    apprun_path.chmod(0o755)

    print("[5/6] Prepare appimagetool")
    extracted = BUILD / "appimagetool-root"
    stale = BUILD / "squashfs-root"
    if extracted.exists():
        shutil.rmtree(extracted)
    if stale.exists():
        shutil.rmtree(stale)
    subprocess.run(
        [str(tool), "--appimage-extract"], cwd=BUILD, check=True,
        stdout=subprocess.DEVNULL,
    )
    BUILD.joinpath("squashfs-root").rename(extracted)
    tool_bin = extracted / "AppRun"
    tool_bin.chmod(0o755)

    print("[6/6] Pack AppImage")
    if OUTPUT.exists():
        OUTPUT.unlink()
    subprocess.run(
        [str(tool_bin), "--no-appstream", str(APPDIR), str(OUTPUT)], check=True
    )

    size = OUTPUT.stat().st_size / (1024 * 1024)
    appdir_size = sum(
        path.stat().st_size for path in APPDIR.rglob("*") if path.is_file()
    ) / (1024 * 1024)
    print(f"\nAppDir payload: {appdir_size:.1f} MiB")
    print(f"Done: {OUTPUT} ({size:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
