"""Сборка SteamSNM-x86_64.AppImage из локального окружения.

Использование:
    python3 tools/build_appimage.py [путь/к/appimagetool.AppImage]

Скрипт упаковывает интерпретатор Python, стандартную библиотеку, PySide6,
библиотеки Qt и плагины Qt внутрь AppDir, затем вызывает appimagetool.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
APPDIR = BUILD / "AppDir"
OUTPUT = ROOT / "SteamSNM-x86_64.AppImage"

PY_VERSION = f"python{sys.version_info.major}.{sys.version_info.minor}"
PY_STDLIB = Path(sys.base_prefix) / "lib" / PY_VERSION
SITE_PACKAGES = PY_STDLIB / "site-packages"
def qt_plugins_dir() -> Path:
    """Плагины Qt: системные (Arch) или из PySide6-колеса (Ubuntu/pip)."""
    system = Path("/usr/lib/qt6/plugins")
    if system.exists():
        return system
    return SITE_PACKAGES / "PySide6" / "Qt" / "plugins"
QT_PLUGIN_DIRS = (
    "platforms", "imageformats", "iconengines", "platformthemes",
    "styles", "egldeviceintegrations", "generic", "tls",
)

# Без OpenSSL Qt Network не может загрузить обложки игр по HTTPS.
TLS_LIBS = ("libssl.so.4", "libcrypto.so.4", "libssl.so.3", "libcrypto.so.3")
TLS_SEARCH_DIRS = (
    Path("/usr/lib"), Path("/usr/lib64"),
    Path("/usr/lib/x86_64-linux-gnu"),
)


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src.resolve(), dst)


def ignored_stdlib(directory: str, names: list[str]) -> set[str]:
    skip = {
        "site-packages", "__pycache__", "test", "idlelib", "tkinter",
        "lib2to3", "ensurepip", "turtledemo", "distutils",
    }
    return {name for name in names if name in skip}


def collect_qt_libs() -> list[Path]:
    """Ищет libQt6*/libshiboken6*, от которых зависят модули и плагины."""
    targets: list[Path] = []
    pyside_dir = SITE_PACKAGES / "PySide6"
    if pyside_dir.exists():
        targets.extend(sorted(pyside_dir.glob("Qt*.so")))
    for name in QT_PLUGIN_DIRS:
        directory = qt_plugins_dir() / name
        if directory.exists():
            targets.extend(sorted(directory.glob("*.so")))

    found: dict[str, Path] = {}
    for target in targets:
        try:
            result = subprocess.run(
                ["ldd", str(target)], capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in result.stdout.splitlines():
            path = line.split("=>")[-1].strip().split(" ")[0] if "=>" in line else line.strip().split(" ")[0]
            if not path.startswith("/"):
                continue
            candidate = Path(path)
            if candidate.name.startswith(("libQt6", "libshiboken6")):
                found[candidate.name] = candidate
    return [found[name] for name in sorted(found)]


def collect_tls_libs() -> list[Path]:
    result: list[Path] = []
    for name in TLS_LIBS:
        for directory in TLS_SEARCH_DIRS:
            candidate = directory / name
            if candidate.exists():
                result.append(candidate.resolve())
                break
    return result


def main() -> int:
    tool = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("/tmp/opencode/appimagetool.AppImage")
    if not tool.exists():
        print(f"appimagetool не найден: {tool}", file=sys.stderr)
        return 1

    if APPDIR.exists():
        shutil.rmtree(APPDIR)
    (APPDIR / "usr/bin").mkdir(parents=True)
    (APPDIR / "usr/lib").mkdir(parents=True)
    (APPDIR / "usr/share/steamsnm/src").mkdir(parents=True)

    print("[1/6] Python interpreter + stdlib")
    executable = Path(sys.executable).resolve()
    shutil.copy2(executable, APPDIR / "usr/bin" / executable.name)
    (APPDIR / "usr/bin/python3").symlink_to(executable.name)
    # Интерпретатор может быть динамически слинкован с libpython. Без неё
    # AppImage не запустится на системах, где нет именно этой версии Python.
    libdir = Path(sysconfig.get_config_var("LIBDIR") or "")
    for name in {
        sysconfig.get_config_var("INSTSONAME"),
        sysconfig.get_config_var("LDLIBRARY"),
    } - {None}:
        candidate = libdir / str(name)
        if candidate.exists():
            shutil.copy2(candidate.resolve(), APPDIR / "usr/lib" / candidate.name)
    shutil.copytree(
        PY_STDLIB, APPDIR / "usr/lib" / PY_VERSION,
        ignore=ignored_stdlib, symlinks=True,
    )

    print("[2/6] PySide6 + shiboken6")
    site_dst = APPDIR / "usr/lib" / PY_VERSION / "site-packages"
    site_dst.mkdir(parents=True, exist_ok=True)
    for package in ("PySide6", "shiboken6"):
        source = SITE_PACKAGES / package
        if source.exists():
            shutil.copytree(source, site_dst / package, symlinks=True)

    print("[3/6] Qt libraries")
    for library in collect_qt_libs() + collect_tls_libs():
        shutil.copy2(library.resolve(), APPDIR / "usr/lib" / library.name)
    for name in QT_PLUGIN_DIRS:
        directory = qt_plugins_dir() / name
        if directory.exists():
            shutil.copytree(
                directory, APPDIR / "usr/lib/qt/plugins" / name,
                symlinks=True, dirs_exist_ok=True,
            )

    print("[4/6] Приложение + иконка")
    shutil.copytree(
        ROOT / "src", APPDIR / "usr/share/steamsnm/src",
        ignore=shutil.ignore_patterns("__pycache__"),
        dirs_exist_ok=True,
    )
    icon = ROOT / "src/resources/icon.png"
    icon_dst = APPDIR / "usr/share/icons/hicolor/256x256/apps/steamsnm.png"
    icon_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon, icon_dst)
    shutil.copy2(icon, APPDIR / "steamsnm.png")
    (APPDIR / ".DirIcon").symlink_to("steamsnm.png")
    metainfo_src = ROOT / "src/resources/SteamSNM.appdata.xml"
    metainfo_dst = APPDIR / "usr/share/metainfo/SteamSNM.appdata.xml"
    metainfo_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(metainfo_src, metainfo_dst)

    desktop = """[Desktop Entry]
Type=Application
Name=SteamSNM
GenericName=Steam Monitor
Comment=Мониторинг цен, скидок и онлайна игр Steam
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
exec "$APPDIR/usr/bin/{executable.name}" "$APPDIR/usr/share/steamsnm/src/main.py" "$@"
"""
    apprun_path = APPDIR / "AppRun"
    apprun_path.write_text(apprun, encoding="utf-8")
    apprun_path.chmod(0o755)

    print("[5/6] Подготовка appimagetool")
    extracted = BUILD / "squashfs-root"
    if not (extracted / "AppRun").exists():
        subprocess.run([str(tool), "--appimage-extract"], cwd=BUILD, check=True,
                       stdout=subprocess.DEVNULL)
    tool_bin = extracted / "AppRun"
    tool_bin.chmod(0o755)

    print("[6/6] Упаковка AppImage")
    if OUTPUT.exists():
        OUTPUT.unlink()
    # appstreamcli --strict некорректно обрабатывает кириллические описания;
    # метаданные всё равно попадают в образ, проверку отключаем.
    subprocess.run([str(tool_bin), "--no-appstream", str(APPDIR), str(OUTPUT)], check=True)

    size = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"\nГотово: {OUTPUT} ({size:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
