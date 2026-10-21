# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build recipe.

    pyinstaller SurfGambit.spec

`browser.py` imports `core.*` and `ui.*` lazily (tkinter is imported inside
`main()`), so both packages are listed as hidden imports: without them the
frozen build starts and then fails on the first navigation.
"""

core_modules = [
    "core.values", "core.storage", "core.store", "core.accounts",
    "core.downloads", "core.search",
    "core.dom", "core.dom.tree", "core.dom.tokenizer", "core.dom.parser",
    "core.style", "core.style.css", "core.style.selectors", "core.style.cascade",
    "core.layout", "core.layout.box", "core.layout.block", "core.layout.inline",
    "core.layout.text", "core.layout.table", "core.layout.flex", "core.layout.grid",
    "core.net", "core.net.client", "core.net.cookies", "core.net.cache",
    "core.js", "core.js.surfscript", "core.js.dom_bridge",
]
ui_modules = [
    "ui.app", "ui.tab", "ui.renderer", "ui.pages", "ui.mascot", "ui.game",
]

a = Analysis(
    ['browser.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=core_modules + ui_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PIL', 'test', 'unittest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SurfGambit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
