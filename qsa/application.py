"""Shared transport-independent application dispatcher, preserving V1 contracts."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse
from .analytics import analyze, stock_detail
from .data import SHANGHAI, attach_reference, validate_dataset
from .version import APP_VERSION
ROOT = Path(__file__).resolve().parents[1]
class Response(NamedTuple):
    status: int
    payload: object
    mime: str = "application/json; charset=utf-8"
class Application:
    def __init__(self, workbench):
        self.workbench = workbench
    def request(self, method, path, payload=None):
        if urlparse(path).path.startswith('/api/v1/'):
            from .router_v2 import dispatch
            return dispatch(self.workbench.v2, method, path, payload)
        if method == 'GET':
            return self._get(path)
        if method == 'POST':
            return self._post(path, payload)
        return Response(405, {'error':'不支持的请求方法'})

    def _get(self, path):
        workbench = self.workbench
        parsed = urlparse(path)
        params = parse_qs(parsed.query)
        path = parsed.path
        try:
            if parsed.path == '/api/status':
                with workbench.lock:
                    return Response(200, {'csrf': workbench.csrf, 'job': workbench.job, 'active_meta': workbench.store.active['meta'], 'today': datetime.now(SHANGHAI).date().isoformat(), 'version': APP_VERSION, 'application_id': 'after-close-lab', 'runtime': 'web'})
            elif parsed.path == '/api/analysis':
                dataset = workbench.store.read(params.get('mode', ['active'])[0])
                return Response(200, analyze(dataset))
            elif parsed.path == '/api/stock':
                dataset = workbench.store.read(params.get('mode', ['active'])[0])
                return Response(200, stock_detail(dataset, params.get('code', [''])[0]))
            elif parsed.path == '/api/stock/history':
                return Response(200, workbench.histories.status(params.get('code', [''])[0], params.get('mode', ['active'])[0], params.get('date', [None])[0]))
            elif parsed.path == '/api/snapshot':
                return Response(200, workbench.store.read(params.get('mode', ['active'])[0]))
            elif parsed.path == '/api/reviews':
                return Response(200, workbench.reviews.status())
            elif parsed.path == '/api/sources':
                return Response(200, workbench.sources.status())
            elif parsed.path == '/api/quant/status':
                return Response(200, workbench.quant.status(params.get('mode', ['active'])[0]))
            elif parsed.path == '/api/quant/stocks':
                return Response(200, workbench.quant.stock_choices(params.get('mode', ['active'])[0], params.get('q', [''])[0]))
            elif parsed.path == '/api/quant/result':
                return Response(200, workbench.quant.store.read_run(params.get('id', [''])[0]))
            elif parsed.path == '/api/quant/export':
                body, mime = workbench.quant.store.export(params.get('id', [''])[0], params.get('format', ['json'])[0])
                return Response(200, body, mime)
            elif parsed.path == '/api/reviews/report':
                return Response(200, workbench.reviews.store.read(params.get('id', [''])[0]))
            elif parsed.path == '/api/reviews/artifact':
                name = params.get('name', [''])[0]
                body = workbench.reviews.store.artifact(params.get('id', [''])[0], name)
                mime = {'svg': 'image/svg+xml', 'png': 'image/png', 'zip': 'application/zip', 'json': 'application/json; charset=utf-8', 'txt': 'text/plain; charset=utf-8'}.get(name.rsplit('.', 1)[-1], 'application/octet-stream')
                return Response(200, body, mime)
            elif parsed.path == '/assets/review-room-v1.png':
                return Response(200, (ROOT / 'static' / 'assets' / 'review-room-v1.png').read_bytes(), 'image/png')
            elif parsed.path in ('/', '/index.html', '/app.js', '/review.js', '/stock.js', '/quant.js', '/sources.js', '/shortline.js', '/style.css', '/review.css', '/quant.css', '/sources.css', '/shortline.css', '/workbench.js', '/workbench.css'):
                filename = 'index.html' if parsed.path == '/' else parsed.path[1:]
                mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8'}[filename.rsplit('.', 1)[1]]
                return Response(200, (ROOT / 'static' / filename).read_bytes(), mime)
            else:
                return Response(404, {'error': '页面不存在'})
        except (ValueError, TypeError, KeyError, OSError) as error:
            return Response(400, {'error': str(error)[:400]})

    def _post(self, path, payload):
        workbench = self.workbench
        parsed = urlparse(path)
        params = parse_qs(parsed.query)
        path = parsed.path
        try:
            if path == '/api/analyze':
                dataset = workbench.store.read(payload.get('mode', 'active'))
                return Response(200, analyze(dataset, payload.get('config')))
            elif path == '/api/sync':
                job = workbench.sync(payload)
                return Response(202, {'message': '同步已启动', 'job': job})
            elif path == '/api/stock/history':
                return Response(202, workbench.histories.start(payload))
            elif path == '/api/import':
                dataset = payload.get('dataset', {})
                validate_dataset(dataset)
                if dataset['meta'].get('mode') != 'demo':
                    dataset['meta']['mode'] = 'import'
                with workbench.lock:
                    workbench.store.save(dataset)
                return Response(200, {'message': '快照已通过结构与价格检查并保存'})
            elif path == '/api/reference':
                dataset = workbench.store.read(payload.get('mode', 'active'))
                if dataset['meta'].get('mode') == 'demo':
                    raise ValueError('演示数据不能进行真实行情核对，请先同步或导入真实快照')
                dataset, count = attach_reference(dataset, payload)
                with workbench.lock:
                    workbench.store.save(dataset)
                return Response(200, {'message': f'已核对{count}只证券的同日收盘价'})
            elif path == '/api/reviews/generate':
                return Response(202, workbench.reviews.start(payload))
            elif path == '/api/reviews/settings':
                return Response(200, workbench.reviews.store.update_settings(payload))
            elif path == '/api/reviews/title':
                return Response(200, workbench.reviews.store.choose_title(payload.get('id'), payload.get('index')))
            elif path == '/api/sources/settings':
                return Response(200, workbench.sources.settings(payload))
            elif path == '/api/sources/test':
                return Response(202, workbench.sources.start(payload))
            elif path == '/api/quant/strategy':
                return Response(200, workbench.quant.store.save_strategy(payload))
            elif path == '/api/quant/stocks/resolve':
                return Response(200, workbench.quant.stock_choices(payload.get('mode', 'active'), values=payload.get('codes', [])))
            elif path == '/api/quant/stocks/check':
                return Response(200, workbench.quant.check_stocks(payload))
            elif path == '/api/quant/run':
                return Response(202, workbench.quant.start(payload))
            elif path == '/api/quant/replay':
                if not payload.get('id'):
                    raise ValueError('请选择需要复现的运行记录')
                return Response(202, workbench.quant.start({}, replay_id=payload.get('id')))
            elif path == '/api/quant/cancel':
                return Response(200, workbench.quant.cancel_run())
            else:
                return Response(404, {'error': '接口不存在'})
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            return Response(400, {'error': str(error)[:400]})
        except Exception:
            return Response(500, {'error': '内部处理失败，原始快照未主动替换；请检查文件格式或重启服务'})
