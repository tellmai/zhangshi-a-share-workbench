# Windows x64 standalone build. Both entry points share the frozen libraries.
from pathlib import Path
import sys
project=Path(SPECPATH)
sys.path.insert(0,str(project/'.vendor'))
from PyInstaller.utils.hooks import collect_all
datas=[(str(project/'static'),'static')]
binaries=[]
hidden=['webview.platforms.winforms','webview.platforms.edgechromium','scripts.source_worker']
for package in ('webview','akshare','baostock','tzdata'):
    d,b,h=collect_all(package)
    datas+=d;binaries+=b;hidden+=h
a=Analysis(['desktop.py'],pathex=[str(project),str(project/'.vendor')],binaries=binaries,
    datas=datas,hiddenimports=hidden,hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['PyQt5','PyQt6','PySide2','PySide6','matplotlib','IPython','pytest'],noarchive=False)
pyz=PYZ(a.pure)
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Zhangshi',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(project/'static/assets/app.ico'))
worker=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ZhangshiWorker',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=True,
    icon=str(project/'static/assets/app.ico'))
coll=COLLECT(gui,worker,a.binaries,a.datas,strip=False,upx=False,name='Zhangshi')
