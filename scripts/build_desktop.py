"""Build a versioned portable bundle containing synthetic seed data only."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from qsa.desktop import desktop_html
from qsa.version import APP_VERSION
from qsa.archive import code_manifest
from qsa.data import make_demo, atomic_json

def main():
    bundle=ROOT/'dist'/('v'+APP_VERSION)/'Zhangshi'
    if not bundle.resolve().is_relative_to((ROOT/'dist').resolve()):
        raise RuntimeError('发行目录越界')
    old_data=bundle/'data'
    if old_data.exists():
        files=list(old_data.rglob('*'))
        seed=old_data/'active.json'
        if any(p.is_file() and p!=seed for p in files) or any(p.is_dir() for p in files):
            raise RuntimeError('发行目录已有运行资料，停止重建；请先完整备份并使用新的发行版本目录。')
        if seed.exists() and json.loads(seed.read_text(encoding='utf-8')).get('meta',{}).get('mode')!='demo':
            raise RuntimeError('发行目录已有真实行情，停止重建，避免覆盖用户资料。')
    (ROOT/'static/desktop.html').write_text(desktop_html(),encoding='utf-8')
    (ROOT/'build').mkdir(exist_ok=True)
    (ROOT/'build'/'build-info.json').write_text(json.dumps(code_manifest(),ensure_ascii=False,indent=2),encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(ROOT/'dist'/('v'+APP_VERSION)),str(ROOT/'Zhangshi.spec')],cwd=ROOT,check=True)
    bundle=ROOT/'dist'/('v'+APP_VERSION)/'Zhangshi'
    seed=bundle/'data';seed.mkdir(parents=True,exist_ok=True)
    if any(seed.iterdir()):
        raise RuntimeError('构建后出现非空种子目录，停止写入，原资料保留。')
    atomic_json(seed/'active.json',make_demo())
    (bundle/'portable.flag').write_text('Portable data lives in the data folder beside Zhangshi.exe.\n',encoding='utf-8')
    (bundle/'使用说明.txt').write_text('涨势 · A股研究台 '+APP_VERSION+'\n\n双击 Zhangshi.exe 启动独立桌面窗口，无需安装 Python。\n请整体保留本文件夹，不能单独移动exe。\n发行包仅含合成演示数据。沿用已有数据可使用 Zhangshi.exe --data-dir 完整数据目录。\n主线工作台、持仓复盘、任务与数据为新增入口；后台调度在设置页手动启用。\n数据保存在data目录，升级前请备份。\nWindows 10/11 64位，需要微软 WebView2 运行时。\n',encoding='utf-8')
    print(bundle,flush=True)

if __name__=='__main__':main()
