# -*- mode: python ; coding: utf-8 -*-
# QuotaHUD 便携版 spec：onefile —— 单个 exe，双击即用（不需要 _internal 目录）
# 适合直接发给同事；缺点是启动时要把运行时解压到临时目录，首次启动慢 1~3 秒。
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web/index.html', 'web'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL.ImageTk'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile：binaries/datas 直接打进 EXE，不生成 _internal，也不做 COLLECT
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='QuotaHUD-Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无黑窗口
    disable_windowed_traceback=False,
    icon='icon.ico',
)
