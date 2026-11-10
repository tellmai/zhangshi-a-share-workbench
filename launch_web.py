"""Dedicated web launcher; reuses only a verified instance of this application."""
import argparse
import json
import sys
import webbrowser
from urllib.request import ProxyHandler, build_opener


def running_instance(url):
    try:
        with build_opener(ProxyHandler({})).open(url+'/api/status',timeout=2) as response:
            status=json.load(response)
        from qsa.version import APP_VERSION
        return isinstance(status,dict) and status.get('application_id')=='after-close-lab' and status.get('version',APP_VERSION)==APP_VERSION
    except (OSError,ValueError):return False


def main():
    parser=argparse.ArgumentParser(description='启动涨势网页版')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('端口需要在1—65535之间')
    url=f'http://127.0.0.1:{args.port}'
    if running_instance(url):
        if not args.no_open:webbrowser.open(url)
        return
    from app import main as serve
    sys.argv=['app.py','--port',str(args.port)]+([] if args.no_open else ['--open'])
    serve()


if __name__=='__main__':main()
