"""WSGI app for signed Telegram access and a public trial. Run with gunicorn.

The separate index.html remains a deliberately public static version. Never serve
that file, repository files or datasets directly from a commercial deployment.
"""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from access_store import AccessStore, PACKAGES
from bot import AdminBot, TelegramAPI

ROOT = Path(__file__).resolve().parent
log = logging.getLogger('hanzi')

@dataclass(frozen=True)
class Config:
    token: str
    admins: frozenset[int]
    app_url: str
    db_path: str
    bot_username: str = ''
    max_age: int = 86400
    polling: bool = False
    support: str = 'Свяжитесь с преподавателем, у которого получили доступ. / Contact the course owner for help.'

    @classmethod
    def from_env(cls):
        token = os.environ.get('BOT_TOKEN', '')
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            raise ValueError('Set BOT_TOKEN in the hosting service secret settings')
        values = [x.strip() for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()]
        if any(not x.isdecimal() or not 0 < int(x) <= 2**52 for x in values):
            raise ValueError('ADMIN_IDS must contain comma-separated numeric Telegram IDs')
        url = os.environ.get('APP_URL') or os.environ.get('RENDER_EXTERNAL_URL', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
            raise ValueError('APP_URL must be the HTTPS origin of this service')
        age = int(os.environ.get('INIT_DATA_MAX_AGE', '86400'))
        if not 60 <= age <= 86400:
            raise ValueError('INIT_DATA_MAX_AGE must be between 60 and 86400 seconds')
        username = os.environ.get('BOT_USERNAME', '').lstrip('@')
        if username and not re.fullmatch('[A-Za-z0-9_]{5,32}', username):
            raise ValueError('Invalid BOT_USERNAME')
        return cls(token, frozenset(map(int, values)), url.rstrip('/'), os.environ.get('ACCESS_DB', str(ROOT / 'var' / 'access.sqlite3')), username, age, os.environ.get('BOT_POLLING', '1') == '1', os.environ.get('SUPPORT_TEXT', cls.support))


def validate_init_data(raw, token, max_age=86400, now=None):
    """Verify Telegram HMAC, age and numeric identity; never trust client user IDs."""
    now = int(time.time()) if now is None else now
    if not raw or len(raw) > 32768:
        raise ValueError('Missing or oversized Telegram authorization')
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    if len(pairs) != len({k for k, _ in pairs}):
        raise ValueError('Duplicate authorization fields')
    values = dict(pairs)
    received = values.pop('hash', '')
    if not re.fullmatch('[a-f0-9]{64}', received):
        raise ValueError('Invalid signature')
    check = '\n'.join(f'{k}={v}' for k, v in sorted(values.items()))
    key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise ValueError('Invalid signature')
    date = int(values.get('auth_date', '0'))
    if date <= 0 or date > now + 30 or now - date > max_age:
        raise ValueError('Telegram session expired')
    user = json.loads(values.get('user', 'null'))
    if not isinstance(user, dict) or type(user.get('id')) is not int or not 0 < user['id'] <= 2**52 or user.get('is_bot'):
        raise ValueError('Invalid Telegram user')
    return user


class CourseRepository:
    def __init__(self, path):
        self.html = Path(path).read_text(encoding='utf-8')
        datasets = {}
        for name, attr in [('DATA','data'),('CURRICULUM','curriculum'),('LOCALE_DATA','locale')]:
            pattern = re.compile(r'const ' + name + r'=BOOT\?BOOT\.' + attr + r':(.*);\n')
            match = pattern.search(self.html)
            if not match:
                raise ValueError('Missing dataset marker: ' + name)
            datasets[attr] = json.loads(match.group(1))
            self.html, count = pattern.subn('const ' + name + '=BOOT.' + attr + ';\n', self.html, count=1)
            assert count == 1
        self.data, self.curriculum, self.locale = datasets['data'], datasets['curriculum'], datasets['locale']

    def shell(self, nonce):
        html = self.html.replace('<head>', '<head><meta name="referrer" content="no-referrer">', 1)
        html = html.replace('<script>\n', '<script>\nwindow.__HANZI_SERVER__=true;\n', 1)
        # Only the app's own script and the official Telegram SDK are permitted.
        return html.replace('<script', '<script nonce="' + nonce + '"')

    def filtered(self, caps):
        book, hsk = caps['book'], caps['hsk']
        def book_source(s):
            if re.fullmatch(r'L\d+', s):
                return 1 <= int(s[1:]) <= book
            return book == 16 and s in {'names', 'extra'}
        data = copy.deepcopy(self.data)
        data['groups'] = [dict(h=g['h'], m=[copy.deepcopy(m) for m in g['m'] if book_source(m['s'])]) for g in self.data['groups'] if any(book_source(m['s']) for m in g['m'])]
        curriculum = copy.deepcopy(self.curriculum)
        curriculum['book'] = [e for e in curriculum['book'] if book_source(e['s'])]
        curriculum['hsk'] = [e for e in curriculum['hsk'] if int(e['s'][1:]) <= hsk]
        words = {g['h'] for g in data['groups']} | {e['h'] for e in curriculum['book']}
        data['examples'] = data['examples'] if book == 16 else [e for e in data['examples'] if all(t in words for t in e['t'])]
        # Short trial phrases only use vocabulary actually included in the trial.
        trials = [
            dict(h='你好吗？',p='Nǐ hǎo ma?',r='Как ты?',t=['你','好','吗'],x='吗 в конце предложения обозначает общий вопрос.'),
            dict(h='汉语很难。',p='Hànyǔ hěn nán.',r='Китайский язык трудный.',t=['汉语','很','难'],x='很 связывает подлежащее с прилагательным; 是 здесь не требуется.')]
        data['examples'] += [e for e in trials if all(t in words for t in e['t'])]
        curriculum['examples'] = [e for e in curriculum['examples'] if (e['course']=='book' and e['lesson'] <= book) or (e['course']=='hsk' and e['lesson'] <= hsk)]
        permitted_grammar = {f'L{n}' for n in range(1,book+1)} | {f'H{n}' for n in range(1,hsk+1)}
        curriculum['grammar'] = {k:v for k,v in curriculum['grammar'].items() if k in permitted_grammar}
        # General source information is public; legacy full-course rule examples are not sent in trial mode.
        if book < 16:
            data['rules'] = []
        available_meanings = {m['r'] for g in data['groups'] for m in g['m']} | {e['r'] for e in curriculum['book']+curriculum['hsk']}
        available_examples = {e['h'] for e in data['examples']+curriculum['examples']}
        locale = copy.deepcopy(self.locale)
        locale['meanings'] = {k:v for k,v in locale['meanings'].items() if k in available_meanings}
        locale['examples'] = {k:v for k,v in locale['examples'].items() if k in available_examples}
        if '你好吗？' in available_examples:
            locale['examples']['你好吗？'] = dict(r='How are you?',x='吗 at the end makes a yes-or-no question.')
        if '汉语很难。' in available_examples:
            locale['examples']['汉语很难。'] = dict(r='Chinese is difficult.',x='很 links the subject to an adjective. 是 is not needed here.')
        locale['grammar'] = {k:v for k,v in locale['grammar'].items() if k in permitted_grammar}
        return dict(data=data, curriculum=curriculum, locale=locale)


class Application:
    def __init__(self, config, repository=None, store=None, api=None):
        self.config = config
        self.repo = repository or CourseRepository(ROOT / 'index.html')
        self.store = store or AccessStore(config.db_path)
        self.bot = AdminBot(api or TelegramAPI(config.token), self.store, config.admins, config.app_url, config.support)
        if config.polling:
            threading.Thread(target=self.bot.run, name='telegram-polling', daemon=True).start()
            if not config.admins:
                log.warning('ADMIN_IDS is empty. Use /id in your bot, then configure the owner ID on the hosting service.')

    def __call__(self, environ, start_response):
        nonce = secrets.token_urlsafe(24)
        headers = [('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer')]
        def respond(status, value, content_type='application/json; charset=utf-8'):
            body = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
            start_response(status, headers+[('Content-Type',content_type),('Content-Length',str(len(body)))])
            return [body]
        try:
            path, method = environ.get('PATH_INFO', '/'), environ.get('REQUEST_METHOD', 'GET')
            if path == '/healthz' and method == 'GET':
                with self.store.connection() as db:
                    db.execute('SELECT 1').fetchone()
                return respond('200 OK', {'ok':True})
            if path in {'/','/index.html'} and method in {'GET','HEAD'}:
                headers.append(('Content-Security-Policy', f"default-src 'self'; script-src 'nonce-{nonce}' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"))
                return respond('200 OK', '' if method=='HEAD' else self.repo.shell(nonce), 'text/html; charset=utf-8')
            if path not in {'/api/bootstrap','/api/access','/api/request'}:
                return respond('404 Not Found', {'error':'not_found'})
            if method != 'POST':
                return respond('405 Method Not Allowed', {'error':'method_not_allowed'})
            origin = environ.get('HTTP_ORIGIN')
            if origin and origin.rstrip('/') != self.config.app_url:
                return respond('403 Forbidden', {'error':'origin_not_allowed'})
            if environ.get('CONTENT_TYPE','').split(';')[0] != 'application/json':
                return respond('415 Unsupported Media Type', {'error':'json_required'})
            length = int(environ.get('CONTENT_LENGTH') or '0')
            if not 0 <= length <= 4096:
                return respond('413 Payload Too Large', {'error':'body_too_large'})
            payload = json.loads(environ['wsgi.input'].read(length) or b'{}')
            if not isinstance(payload,dict):
                return respond('400 Bad Request', {'error':'invalid_body'})
            auth = environ.get('HTTP_AUTHORIZATION','')
            user = None
            if auth:
                if not auth.startswith('tma '):
                    raise ValueError('Invalid authorization')
                user = validate_init_data(auth[4:], self.config.token, self.config.max_age)
                self.store.register(user, payload.get('language'))
            caps = self.store.permissions(user['id'] if user else None, admin=bool(user and user['id'] in self.config.admins))
            if path == '/api/request':
                if not user:
                    return respond('401 Unauthorized', {'error':'open_in_telegram'})
                package = payload.get('package')
                if not isinstance(package,str) or package not in PACKAGES:
                    return respond('400 Bad Request', {'error':'unknown_package'})
                fresh = self.store.request(user['id'], package)
                return respond('200 OK', {'ok':True,'duplicate':not fresh})
            if path == '/api/access':
                return respond('200 OK', caps)
            result = self.repo.filtered(caps)
            result.update(access=caps, user={'id':user['id']} if user else None, bot_url='https://t.me/'+self.config.bot_username if self.config.bot_username else None)
            return respond('200 OK',result)
        except PermissionError:
            return respond('403 Forbidden', {'error':'account_restricted'})
        except (ValueError, TypeError, OverflowError):
            return respond('401 Unauthorized', {'error':'invalid_or_expired_telegram_session'})
        except Exception:
            # Do not log request headers, initData or Telegram URLs.
            log.error('Request failed; permissions remain restricted')
            return respond('500 Internal Server Error', {'error':'server_error'})


def create_app():
    """Gunicorn factory: exactly one worker, with multiple HTTP threads."""
    return Application(Config.from_env())


if __name__ == '__main__':
    import argparse
    from wsgiref.simple_server import make_server
    parser = argparse.ArgumentParser(description='Local development server; use gunicorn in production')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    app = create_app()
    print(f'Local test server on http://127.0.0.1:{args.port}')
    with make_server('127.0.0.1', args.port, app) as httpd:
        httpd.serve_forever()
