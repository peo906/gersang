import copy
import json
import secrets
import os
import threading
import webbrowser
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from collector import Collector, populate_durations
from optimizer import integer, key, solve

ROOT = Path(sys.executable).resolve().parent.parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PORT = 8765
STATE = {'busy': False, 'message': '아이템을 입력하세요. 저장된 데이터가 있으면 DB에서 불러옵니다.', 'error': '', 'data': None, 'revision': 0, 'history': []}
LOCK = threading.Lock()
STOP = threading.Event()
DESKTOP_TOKEN = os.environ.get("GERSANG_DESKTOP_TOKEN")
TOKEN = secrets.token_urlsafe(24)


def recent_history(items, name):
    name = name.strip()
    return ([name] + [n for n in items if key(n) != key(name)])[:5]


def save_history(items):
    folder = ROOT / 'data'; folder.mkdir(exist_ok=True)
    temporary = folder / 'history.tmp'
    temporary.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / 'history.json')


def save(data):
    folder = ROOT / 'data'; folder.mkdir(exist_ok=True)
    temporary = folder / 'latest.tmp'
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / 'latest.json')


def scan(name, mode, current, node, query):
    def progress(message):
        with LOCK:
            STATE['message'] = message
    try:
        data = Collector(progress, STOP).collect(name, mode, current, node, query)
        save(data)
        with LOCK:
            if mode == 'search':
                history = recent_history(STATE['history'], data['items'][data['target']]['name'])
                save_history(history)
                STATE['history'] = history
            STATE.update(data=data, message='준비 완료 · 저장된 데이터는 요청 없이 다시 조회하지 않습니다.', revision=STATE['revision'] + 1)
    except Exception as e:
        with LOCK:
            STATE.update(error=str(e), message='조회가 중단되었습니다. 이전 완료 데이터가 있으면 유지됩니다.')
    finally:
        with LOCK:
            STATE['busy'] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, value, mime='application/json; charset=utf-8'):
        content = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': '허용되지 않은 호스트'})
        if self.path == '/':
            html = (ROOT / 'index.html').read_text(encoding='utf-8').replace('__LOCAL_TOKEN__', TOKEN)
            return self.send(200, html, 'text/html; charset=utf-8')
        path = urlsplit(self.path)
        if path.path == '/api/state':
            revision = parse_qs(path.query).get('revision', [''])[0]
            with LOCK:
                state = {k: copy.deepcopy(v) for k, v in STATE.items()
                         if k != 'data' or revision != str(STATE['revision'])}
            return self.send(200, state)
        return self.send(404, {'error': '없는 경로'})

    def do_POST(self):
        if self.path == '/api/desktop/close':
            if not self.valid_host() or not (DESKTOP_TOKEN and secrets.compare_digest(DESKTOP_TOKEN, self.headers.get('X-Desktop-Token', ''))):
                return self.send(403, {'error': '데스크톱 연결 인증 실패'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4000000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('잘못된 데스크톱 응답')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('잘못된 데스크톱 응답')
                STOP.set()
                self.send(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            except (ValueError, TypeError):
                return self.send(400, {'error': '잘못된 데스크톱 응답'})
        if (not self.valid_host() or self.headers.get('X-Local-Token') != TOKEN
            or self.headers.get('Origin') not in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}')
            or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
            return self.send(403, {'error': '로컬 화면에서만 요청할 수 있습니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100000:
                raise ValueError('잘못된 요청 크기')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('잘못된 요청')
            if self.path == '/api/scan':
                name = body.get('name', '')
                if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
                    raise ValueError('아이템 이름을 1~100자로 입력하세요.')
                with LOCK:
                    if STATE['busy']:
                        return self.send(409, {'error': '이미 조회 중입니다.'})
                    mode = body.get('mode', 'search')
                    if mode not in ('search', 'market', 'recipe', 'expand', 'finished', 'fallback'):
                        raise ValueError('잘못된 조회 방식')
                    current = copy.deepcopy(STATE['data'])
                    if mode != 'search':
                        if not current:
                            raise ValueError('먼저 아이템을 검색하세요.')
                        name = current['items'][current['target']]['name']
                    node = body.get('node')
                    if mode in ('expand', 'fallback') and (not isinstance(node, str) or not current or node not in current['items'] or node == current['target']):
                        raise ValueError('올바른 조합 재료를 선택하세요.')
                    query = {'quantity': integer(body.get('quantity', 1), '목표 수량', 1, 10000),
                             'owned': body.get('owned', {}), 'modes': body.get('modes', {}), 'outputs': body.get('outputs', {})}
                    if any(not isinstance(query[k], dict) for k in ('owned', 'modes', 'outputs')):
                        raise ValueError('잘못된 제작 계획 형식')
                    for value in query['owned'].values():
                        integer(value, '보유 수량', 0, 10**9)
                    if any(not isinstance(n, str) or m not in ('buy', 'craft', 'auto') for n, m in query['modes'].items()):
                        raise ValueError('잘못된 계산 방식')
                    for outputs in query['outputs'].values():
                        if not isinstance(outputs, list):
                            raise ValueError('잘못된 산출량 형식')
                        for value in outputs:
                            integer(value, '산출량', 1, 100000)
                    STATE.update(busy=True, error='', message='저장된 재료 DB 확인 중입니다.')
                    STOP.clear()
                    threading.Thread(target=scan, args=(name.strip(), mode, current, node, query), daemon=True).start()
                return self.send(202, {'ok': True})
            if self.path == '/api/stop':
                STOP.set()
                return self.send(200, {'ok': True})
            if self.path == '/api/history':
                name = body.get('remove')
                if name is not None and not isinstance(name, str):
                    raise ValueError('잘못된 검색 기록')
                with LOCK:
                    history = [n for n in STATE['history'] if key(n) != key(name)] if name is not None else []
                    save_history(history)
                    STATE['history'] = history
                return self.send(200, {'history': history})
            if self.path == '/api/calculate':
                with LOCK:
                    if STATE['busy']:
                        raise ValueError('조회가 끝난 후 계산하세요.')
                    data = copy.deepcopy(STATE['data'])
                if not data:
                    raise ValueError('먼저 아이템을 조회하세요.')
                owned = body.get('owned', {})
                if not isinstance(owned, dict):
                    raise ValueError('보유 수량 형식 오류')
                outputs = body.get('outputs', {})
                if not isinstance(outputs, dict):
                    raise ValueError('제작 결과 수량 형식 오류')
                for n, values in outputs.items():
                    if n not in data['items'] or not isinstance(values, list) or len(values) != len(data['items'][n]['recipes']):
                        raise ValueError('제작법 수량 형식 오류')
                    for recipe, output in zip(data['items'][n]['recipes'], values):
                        recipe['output'] = integer(output, '제작 결과 수량', 1, 100000)
                modes = body.get('modes', {})
                if not isinstance(modes, dict):
                    raise ValueError('계산 방식 형식 오류')
                for n in data['items']:
                    if n != data['target']:
                        modes.setdefault(n, 'buy')
                return self.send(200, solve(data, data['target'], body.get('quantity', 1), owned,
                                            modes, body.get('salePrice')))
            return self.send(404, {'error': '없는 경로'})
        except (ValueError, KeyError, TypeError) as e:
            return self.send(400, {'error': str(e)})
        except Exception as e:
            return self.send(500, {'error': '계산 오류: ' + str(e)})


if __name__ == '__main__':
    if '--portable-self-test' in sys.argv:
        from portable_check import main
        main()
        raise SystemExit(0)
    history_file = ROOT / 'data' / 'history.json'
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding='utf-8'))
            if isinstance(history, list) and all(isinstance(n, str) for n in history):
                for name in reversed(history):
                    if name.strip():
                        STATE['history'] = recent_history(STATE['history'], name)
        except (OSError, ValueError):
            pass
    cached = ROOT / 'data' / 'latest.json'
    if cached.exists():
        try:
            previous = json.loads(cached.read_text(encoding='utf-8'))
            if previous.get('scope') == 'first-page':
                for item in previous['items'].values():
                    populate_durations(item)
                STATE.update(data=previous, revision=1,
                             message='저장된 DB 화면입니다. 요청 없이 다시 조회하지 않습니다.')
                if not history_file.exists():
                    STATE['history'] = [previous['items'][previous['target']]['name']]
        except (OSError, ValueError):
            STATE['error'] = '이전 데이터 파일을 읽지 못했습니다. 새로 조회하세요.'
    try:
        server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        print('8765 포트를 사용 중입니다. 이미 실행 중인 프로그램을 확인하세요.')
        raise SystemExit(1)
    print(f'거상 제작비 계산기: http://127.0.0.1:{PORT}', flush=True)
    print('종료: 이 창에서 Ctrl+C. 조회는 새로 조회 버튼을 눌러야 시작합니다.', flush=True)
    if '--desktop' not in sys.argv:
        webbrowser.open(f'http://127.0.0.1:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        STOP.set()
    finally:
        server.server_close()
