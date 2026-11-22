# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe: one-file LiveClip.exe with a bundled ffmpeg.

Drop ffmpeg.exe (and ffprobe.exe) into packaging/pyinstaller/bin/ before
building; they are copied into the app and picked up by autoclip.media.
"""

import glob
import os

from PyInstaller.utils.hooks import collect_data_files

spec_dir = os.path.abspath(SPECPATH)  # noqa: F821
project_root = os.path.abspath(os.path.join(spec_dir, "..", ".."))

bundled_tools = [(path, "bin") for path in glob.glob(os.path.join(spec_dir, "bin", "*"))]

# Listed explicitly: collecting kivy or autoclip.ui as submodules imports them,
# and importing Kivy without a display kills the build subprocess.
a = Analysis(
    [os.path.join(project_root, "main.py")],
    pathex=[project_root],
    binaries=bundled_tools,
    datas=collect_data_files("kivy") + collect_data_files("autoclip"),
    hiddenimports=[
        "autoclip.android",
        "autoclip.cli",
        "autoclip.clip",
        "autoclip.config",
        "autoclip.detect",
        "autoclip.live",
        "autoclip.media",
        "autoclip.plan",
        "autoclip.transcribe",
        "autoclip.ui.app",
    ],
    # Our hook-kivy.py replaces PyInstaller's, which imports Kivy and needs a
    # display; the build then works on a headless Windows runner.
    hookspath=[os.path.join(spec_dir, "hooks")],
    runtime_hooks=[],
    # faster-whisper is optional and drags in ~90 MB of ctranslate2; the
    # frozen app uses the HTTP caption path. Run from source for offline ASR.
    excludes=[
        "pytest",
        "tkinter",
        "faster_whisper",
        "ctranslate2",
        "torch",
        "transformers",
        "tokenizers",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LiveClip",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
