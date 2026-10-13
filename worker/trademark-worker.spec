# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

rapidocr_datas = collect_data_files(
    "rapidocr", include_py_files=False
)
rapidocr_binaries = []
rapidocr_hiddenimports = collect_submodules("rapidocr.inference_engine.onnxruntime")

analysis = Analysis(
    ["src/trademark_worker/__main__.py"],
    pathex=["src"],
    binaries=rapidocr_binaries,
    datas=rapidocr_datas,
    # Playwright provides its own PyInstaller hooks.  collect_all("playwright")
    # duplicates those hooks and makes the frozen Node driver hang at startup.
    hiddenimports=["comtypes.gen.UIAutomationClient", *rapidocr_hiddenimports],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="trademark-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="trademark-worker",
)
