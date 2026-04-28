# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['Crypto.Cipher.AES']
hiddenimports += collect_submodules('watchdog')
hiddenimports += collect_submodules('pystray')
hiddenimports += collect_submodules('PIL')


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('logo.png', '.'), ('logo.ico', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='AutoNcm2Mp3',
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
    icon=['logo.ico'],
)
