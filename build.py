#!/usr/bin/env python3
"""Build a standalone executable with PyInstaller.

    python build.py                      # PixelBench, one-folder build in dist/PixelBench/
    python build.py vectorbench          # VectorBench, one-folder build in dist/VectorBench/
    python build.py vectorbench --onefile  # single executable (slower to start)

Run it on the OS you are targeting: Windows for Orcastrader/aaagc, Linux for Alex.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


APPS = {"pixelbench": "PixelBench", "vectorbench": "VectorBench"}


def main():
    onefile = "--onefile" in sys.argv
    app = next((a for a in sys.argv[1:] if a.lower() in APPS), "pixelbench").lower()
    name = APPS[app]
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", name,
        "--windowed" if sys.platform != "linux" else "--noconsole",
        "--onefile" if onefile else "--onedir",
        "--collect-submodules", "psd_tools",
        "--collect-submodules", "scipy.ndimage",
        "--hidden-import", "PIL.ImageQt",
        # trim Qt modules we do not use to keep the bundle small
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "tkinter",
        os.path.join(HERE, f"{app}.py"),
    ]
    icon = os.path.join(HERE, "assets", f"{app}.ico")
    if os.path.exists(icon):
        args[-1:-1] = ["--icon", icon]
    print(" ".join(args))
    subprocess.check_call(args, cwd=HERE)
    out = os.path.join(HERE, "dist", name + (".exe" if sys.platform == "win32" and onefile else ""))
    print(f"\nBuilt: {out}")
    for d in ("build",):
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)


if __name__ == "__main__":
    main()
