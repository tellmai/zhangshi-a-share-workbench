"""Build the desktop bundle and seed a portable copy with this project's data."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from qsa.desktop import desktop_html

def main():
    (ROOT/'static/desktop.html').write_text(desktop_html(),encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',str(ROOT/'Zhangshi.spec')],cwd=ROOT,check=True)
    bundle=ROOT/'dist/Zhangshi'
    shutil.copytree(ROOT/'data',bundle/'data',dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('desktop-profile','*.log','*.tmp'))
    (bundle/'portable.flag').write_text('Portable data lives in the data folder beside Zhangshi.exe.\n',encoding='utf-8')
    (bundle/'使用说明.txt').write_text('涨势 · A股研究台 0.6.0\n\n双击 Zhangshi.exe 启动独立桌面窗口，无需安装 Python。\n请整体保留本文件夹，不能单独移动exe。\ndata 文件夹保存行情、策略与复盘记录，升级前请备份。\nWindows 10/11 64位，需要微软 WebView2 运行时。\n',encoding='utf-8')
    print(bundle,flush=True)

if __name__=='__main__':main()
