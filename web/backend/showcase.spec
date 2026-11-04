# PyInstaller onedir. Run from repository root after building React.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).resolve().parents[1]
datas = [
    (str(root / "web" / "frontend" / "dist"), "web/frontend/dist"),
    (str(root / "pc" / "schema.sql"), "pc"),
]
a = Analysis([str(root / "web" / "backend" / "showcase_entry.py")], pathex=[str(root), str(root / "web" / "backend")], binaries=[], datas=datas, hiddenimports=collect_submodules("uvicorn"), hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="RFIDShowcase", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RFIDShowcase")
