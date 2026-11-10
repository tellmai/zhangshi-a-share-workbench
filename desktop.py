"""Windows entry point, including an isolated bundled data-worker mode."""
from __future__ import annotations
import argparse
import ctypes
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
if (ROOT/'.vendor').exists():sys.path.insert(0,str(ROOT/'.vendor'))


def main():
    if '--source-worker' in sys.argv:
        from scripts.source_worker import main as worker_main
        worker_main();return
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir');parser.add_argument('--self-test',metavar='REPORT_JSON')
    args=parser.parse_args()
    from qsa.desktop import APP_TITLE,DesktopBridge,RESOURCES,data_directory,desktop_html
    data=data_directory(args.data_dir);data.mkdir(parents=True,exist_ok=True)
    log=data/'desktop.log'
    logging.basicConfig(filename=log,level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')
    mutex=None
    try:
        if os.name=='nt' and not args.self_test:
            from ctypes import wintypes
            ctypes.windll.kernel32.CreateMutexW.restype=wintypes.HANDLE
            ctypes.windll.kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Zhangshi.AfterCloseLab')
            mutex=ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\Zhangshi.AfterCloseLab.Desktop')
            if ctypes.windll.kernel32.GetLastError()==183:
                hwnd=ctypes.windll.user32.FindWindowW(None,APP_TITLE)
                if hwnd:ctypes.windll.user32.ShowWindow(hwnd,9);ctypes.windll.user32.SetForegroundWindow(hwnd)
                return
        import webview
        from app import Workbench
        workbench=Workbench(data)
        bridge=DesktopBridge(workbench)
        # Keep generated HTML alongside assets, so normal relative asset paths work.
        entry=RESOURCES/'static'/'desktop.html'
        if not getattr(sys,'frozen',False):entry.write_text(desktop_html(),encoding='utf-8')
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['ALLOW_FILE_URLS']=True
        window=webview.create_window(APP_TITLE,entry.as_uri(),js_api=bridge,width=1440,height=960,
            min_size=(980,700),background_color='#f5f8f8',hidden=bool(args.self_test))
        bridge._window=window
        def closing():
            busy=workbench.quant.job.get('state')=='running' or workbench.job.get('state')=='running' or workbench.sources.job.get('state')=='running'
            if busy and not window.create_confirmation_dialog('退出研究台','当前有任务正在运行。退出会取消或中断任务，是否继续？'):return False
        window.events.closing+=closing
        workbench.reviews.start_scheduler()
        def smoke():
            report={'ok':False}
            try:
                for _ in range(100):
                    time.sleep(.3)
                    ready=window.evaluate_js('Boolean(window.__ZHANGSHI_DESKTOP__ && typeof state!=="undefined" && state.analysis && state.status)')
                    if ready:break
                if not ready:raise RuntimeError('桌面首屏未完成加载')
                report=window.evaluate_js('({ok:true,desktop:state.status.desktop,version:state.status.version,trade_date:state.analysis.meta.trade_date,stocks:state.analysis.quality.actual_count,nav:document.querySelectorAll("#navigation a").length,location:location.protocol})')
                window.evaluate_js('location.hash="quant"')
                for _ in range(70):
                    time.sleep(.2)
                    if window.evaluate_js('Boolean(quantState.catalog && quantState.config && document.querySelector(".q-stock-picker"))'):break
                else:raise RuntimeError('策略实验室未完成加载')
                report['quant']=True
            except Exception as error:report={'ok':False,'error':str(error)};logging.exception('Desktop self-test')
            finally:
                Path(args.self_test).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                window.destroy()
        try:
            webview.start(smoke if args.self_test else None,gui='edgechromium',private_mode=False,
                storage_path=str(data/'desktop-profile'),icon=str(RESOURCES/'static'/'assets'/'app.ico'),
                localization={'global.quitConfirmation':'确定退出研究台？','global.ok':'确定','global.cancel':'取消'})
        finally:
            workbench.quant.cancel.set()
            worker=getattr(workbench.quant,'worker',None)
            if worker:worker.join(5)
            workbench.histories.close();workbench.reviews.close()
    except Exception as error:
        logging.exception('Desktop startup failed')
        if args.self_test:Path(args.self_test).write_text(json.dumps({'ok':False,'error':str(error)}),encoding='utf-8')
        elif os.name=='nt':ctypes.windll.user32.MessageBoxW(None,f'启动未完成：{error}\n\n日志：{log}', '涨势 · 启动提示',0x10)
        raise
    finally:
        if mutex:ctypes.windll.kernel32.CloseHandle(mutex)


if __name__=='__main__':main()
