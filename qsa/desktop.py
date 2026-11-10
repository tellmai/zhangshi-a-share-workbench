"""Native desktop transport: dispatch services in memory, with no HTTP listener."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

APP_TITLE = "涨势 · A股研究台"
from .version import APP_VERSION as VERSION
RESOURCES = Path(__file__).resolve().parents[1]


def data_directory(override=None):
    if override: return Path(override).resolve()
    if not getattr(sys,"frozen",False): return RESOURCES/"data"
    home=Path(sys.executable).parent
    if (home/"portable.flag").exists(): return home/"data"
    return Path(os.environ.get("LOCALAPPDATA",Path.home()/"AppData"/"Local"))/"Zhangshi"/"data"


class DesktopBridge:
    def __init__(self,workbench):
        self._workbench=workbench
        self._application=workbench.application
        self._window=None

    def _dispatch(self,path,body=None):
        if not isinstance(path,str) or len(path)>4096 or not path.startswith('/api/') or urlparse(path).netloc:
            raise ValueError("无效的本机功能路径")
        if body is not None:
            raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            if len(raw)>16*1024*1024: raise ValueError("导入内容过大")
            if not isinstance(body,dict): raise ValueError("请求内容必须为对象")
        response=self._application.request('GET' if body is None else 'POST',path,body)
        return response.status,response.payload,response.mime

    def request(self,path,body=None):
        try:
            status,payload,mime=self._dispatch(path,body)
            if isinstance(payload,bytes): raise ValueError("文件请使用导出功能")
            if path.startswith('/api/status') and status==200:
                payload={**payload,"desktop":True,"runtime":"desktop","version":VERSION,"data_directory":str(self._workbench.store.root)}
            if status==200 and isinstance(payload,dict) and payload.get('cover') and payload.get('id'):
                try:
                    raw=self._workbench.reviews.store.artifact(payload['id'],'cover.svg')
                    payload={**payload,'desktop_cover':'data:image/svg+xml;base64,'+base64.b64encode(raw).decode()}
                except (ValueError,OSError): pass
            return {"status":status,"data":payload}
        except Exception as error: return {"status":400,"data":{"error":str(error)[:400]}}

    def save_artifact(self,path,name):
        status,payload,mime=self._dispatch(path)
        if status!=200: raise ValueError((payload or {}).get('error','导出失败'))
        if not isinstance(payload,bytes): payload=json.dumps(payload,ensure_ascii=False,indent=2).encode('utf-8')
        return self._save(name,payload)

    def save_file(self,name,encoded):
        if not isinstance(encoded,str) or len(encoded)>90*1024*1024: raise ValueError("导出文件过大")
        return self._save(name,base64.b64decode(encoded,validate=True))

    def _save(self,name,raw):
        import webview
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(name))[:180].strip(' .') or '导出文件'
        paths=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=name)
        if not paths:return {"saved":False}
        path=Path(paths[0] if isinstance(paths,(list,tuple)) else paths)
        path.write_bytes(raw)
        return {"saved":True,"path":str(path)}

    def open_data_folder(self):
        os.startfile(str(self._workbench.store.root))
        return True


def desktop_html():
    html=(RESOURCES/'static'/'index.html').read_text(encoding='utf-8')
    # A local file document with native bridge calls: no port, server or browser tab.
    html=html.replace('href="/','href="').replace('src="/','src="')
    html=html.replace('<script src="review.js"','<script src="desktop.js"></script>\n  <script src="review.js"',1)
    html=re.sub(r'<b>v[\d.]+</b>','<b>桌面版 v'+VERSION+'</b>',html)
    return html
