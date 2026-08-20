# -*- mode: python ; coding: utf-8 -*-
"""Build Coco's Python services into one shared onedir bundle."""

import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


ROOT = Path(SPEC).resolve().parents[2]

TUTOR_ENTRY = (
    ROOT / "lib" / "proactive_tutor" / "proactive_tutor" / "packaged_entrypoint.py"
)
SENSING_ENTRY = ROOT / "lib" / "sensing" / "sensing" / "sensing_server.py"
WAKE_WORD_ENTRY = ROOT / "lib" / "sensing" / "sensing" / "wake_word_worker.py"
VISUAL_COPILOT_ENTRY = ROOT / "src" / "visual_copilot" / "server.py"

COMMON_HIDDEN_IMPORTS = [
    # uvicorn internals
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # starlette internals used by fastapi
    "starlette.responses",
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.cors",
    # LLM / tokenizers
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    # shared workspace packages
    "external_api",
    "py_utils",
]

TUTOR_HIDDEN_IMPORTS = COMMON_HIDDEN_IMPORTS + [
    "proactive_tutor",
    "memory",
    "memory_mcp.server",
]

SENSING_HIDDEN_IMPORTS = COMMON_HIDDEN_IMPORTS + [
    # async DB / cache
    "aiosqlite",
    "greenlet",
    "redis",
    "redis.asyncio",
    "redis.asyncio.client",
    # workspace and image processing packages
    "sensing",
    "numpy",
    "PIL",
    "shapely",
    "mss",
]

WAKE_WORD_HIDDEN_IMPORTS = [
    "sensing",
    "numpy",
    "sherpa_onnx",
    "sherpa_onnx.lib._sherpa_onnx",
]

VISUAL_COPILOT_HIDDEN_IMPORTS = COMMON_HIDDEN_IMPORTS + [
    "visual_copilot",
    "openai",
    "mss",
    "PIL",
]

if platform.system() == "Darwin":
    SENSING_HIDDEN_IMPORTS += [
        "Quartz",
        "objc",
        "pynput",
        "pynput.keyboard",
        "pynput.mouse",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ]
elif platform.system() == "Windows":
    SENSING_HIDDEN_IMPORTS += [
        "pynput",
        "pynput.keyboard",
        "pynput.mouse",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "ctypes",
        "ctypes.wintypes",
    ]
elif platform.system() == "Linux":
    SENSING_HIDDEN_IMPORTS += [
        "pynput",
        "pynput.keyboard",
        "pynput.mouse",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
    ]

tutor_datas = collect_data_files("proactive_tutor")
tutor_datas += collect_data_files("litellm")

sensing_datas = collect_data_files("sensing")
sensing_datas += collect_data_files("litellm")

wake_word_datas = collect_data_files("sensing")
wake_word_datas += collect_data_files("sherpa_onnx")

visual_copilot_datas = collect_data_files("visual_copilot")

tutor = Analysis(
    [str(TUTOR_ENTRY)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=tutor_datas,
    hiddenimports=TUTOR_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

sensing = Analysis(
    [str(SENSING_ENTRY)],
    pathex=[str(ROOT)],
    binaries=collect_dynamic_libs("shapely"),
    datas=sensing_datas,
    hiddenimports=SENSING_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pandas"],
    noarchive=False,
    optimize=0,
)

wake_word = Analysis(
    [str(WAKE_WORD_ENTRY)],
    pathex=[str(ROOT)],
    binaries=collect_dynamic_libs("sherpa_onnx"),
    datas=wake_word_datas,
    hiddenimports=WAKE_WORD_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pandas"],
    noarchive=False,
    optimize=0,
)

visual_copilot = Analysis(
    [str(VISUAL_COPILOT_ENTRY)],
    pathex=[str(ROOT / "src"), str(ROOT)],
    binaries=[],
    datas=visual_copilot_datas,
    hiddenimports=VISUAL_COPILOT_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pandas"],
    noarchive=False,
    optimize=0,
)

tutor_pyz = PYZ(tutor.pure)
sensing_pyz = PYZ(sensing.pure)
wake_word_pyz = PYZ(wake_word.pure)
visual_copilot_pyz = PYZ(visual_copilot.pure)

tutor_exe = EXE(
    tutor_pyz,
    tutor.scripts,
    [],
    exclude_binaries=True,
    name="tutor-server",
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

sensing_exe = EXE(
    sensing_pyz,
    sensing.scripts,
    [],
    exclude_binaries=True,
    name="sensing-server",
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

wake_word_exe = EXE(
    wake_word_pyz,
    wake_word.scripts,
    [],
    exclude_binaries=True,
    name="wake-word-worker",
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

visual_copilot_exe = EXE(
    visual_copilot_pyz,
    visual_copilot.scripts,
    [],
    exclude_binaries=True,
    name="visual-copilot-server",
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

# A single COLLECT gives both executables one shared _internal directory. Files
# with the same destination (Python, LiteLLM, Pillow, NumPy, and so on) are
# collected only once.
bundle = COLLECT(
    tutor_exe,
    sensing_exe,
    wake_word_exe,
    visual_copilot_exe,
    tutor.binaries,
    tutor.datas,
    sensing.binaries,
    sensing.datas,
    wake_word.binaries,
    wake_word.datas,
    visual_copilot.binaries,
    visual_copilot.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="coco-services",
)
