"""Private-chat administration via Telegram Bot API long polling."""
from __future__ import annotations
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from access_store import PACKAGES

log = logging.getLogger('hanzi.bot')

class TelegramAPI:
    def __init__(self, token):
        self._root = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **payload):
        request = urllib.request.Request(self._root + method, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError):
            # urllib exceptions can contain a request URL with the bot token. Never log them.
            raise RuntimeError('Telegram request failed') from None
        if not data.get('ok'):
            raise RuntimeError('Telegram rejected request')
        return data['result']


def button(text, data):
    return {'text': text, 'callback_data': data}


class AdminBot:
    def __init__(self, api, store, admins, app_url, support_text='Свяжитесь с преподавателем, у которого получили доступ. / Contact the course owner for help.'):
        self.api, self.store, self.admins, self.app_url = api, store, frozenset(admins), app_url
        self.support_text = support_text
        self.stop = threading.Event()

    def send(self, uid, text, rows=None):
        payload = dict(chat_id=uid, text=text)
        if rows:
            payload['reply_markup'] = {'inline_keyboard': rows}
        return self.api.call('sendMessage', **payload)

    def home(self, uid):
        self.send(uid, 'Управление доступом. Выдавайте пакеты после подтверждённой оплаты.\nАдминистраторы задаются только в настройках сервера.', [
            [button('Заявки', 'requests:0'), button('Ученики', 'users:0')],
            [button('Как найти ученика', 'help')]])

    def show_list(self, uid, kind, offset=0):
        if offset < 0 or offset > 100000:
            return
        values = self.store.pending(offset) if kind == 'requests' else self.store.list_users(offset)
        rows = []
        for row in values:
            target = row['user_id'] if kind == 'requests' else row['id']
            label = (row['name'] or row['username'] or str(target))[:30]
            if kind == 'requests':
                label += ' · ' + PACKAGES[row['package']][0]
            rows.append([button(label, f'user:{target}')])
        nav = []
        if offset:
            nav.append(button('←', f'{kind}:{max(0,offset-8)}'))
        if len(values) == 8:
            nav.append(button('→', f'{kind}:{offset+8}'))
        if nav:
            rows.append(nav)
        rows.append([button('Главное меню', 'home')])
        self.send(uid, ('Заявки' if kind == 'requests' else 'Ученики') + (' — пока пусто' if not values else ''), rows)

    def show_user(self, actor, uid):
        user = self.store.user(uid)
        if not user:
            self.send(actor, 'Ученик ещё не открывал бота или приложение.')
            return
        caps = self.store.permissions(uid)
        status = 'ЗАБЛОКИРОВАН' if caps['blocked'] else 'Активен'
        lines = [f"{user['name']} @{user['username']}\nID: {uid}\n{status}"]
        for g in caps['grants']:
            date = datetime.fromtimestamp(g['expires'], timezone.utc).strftime('%Y-%m-%d UTC') if g['expires'] else 'бессрочно'
            lines.append(g['name'] + ' — ' + date)
        rows = [[button('Открыть: ' + name, f'pick:{uid}:{pkg}')] for pkg,(name,_,_) in PACKAGES.items()]
        rows += [[button('Закрыть: ' + g['name'], f'revoke:{uid}:{g["package"]}')] for g in caps['grants']]
        rows += [[button('Разблокировать' if caps['blocked'] else 'Блокировать все курсы', f'{"unblock" if caps["blocked"] else "block"}:{uid}')], [button('Главное меню', 'home')]]
        self.send(actor, '\n'.join(lines), rows)

    def user_home(self, user):
        uid = user['id']
        en = str(user.get('language_code', '')).startswith('en')
        rows = [[{'text': 'Open lessons' if en else 'Открыть уроки', 'web_app': {'url': self.app_url}}], [button('Request access' if en else 'Запросить доступ', 'request-menu')]]
        if uid in self.admins:
            rows.append([button('Управление доступом', 'home')])
        self.send(uid, 'Chinese practice. Trial: textbook lessons 1–3. /id shows your Telegram ID.' if en else 'Тренажёр китайского. Пробный доступ: уроки 1–3. Команда /id покажет ваш Telegram ID.', rows)

    def request_menu(self, uid):
        self.send(uid, 'Выберите пакет / Choose a package', [[button(name, 'ask:' + key)] for key,(name,_,_) in PACKAGES.items()])

    def handle(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        user = callback.get('from', {}) if callback else message.get('from', {})
        uid = user.get('id')
        # Both actor and the private chat must match, including for inline callbacks.
        if type(uid) is not int or message.get('chat', {}).get('type') != 'private' or message['chat'].get('id') != uid:
            return
        self.store.register(user)
        if callback:
            data = callback.get('data', '')
            if not isinstance(data, str) or len(data) > 64:
                return
            self.api.call('answerCallbackQuery', callback_query_id=callback['id'])
            if data == 'request-menu':
                self.request_menu(uid)
                return
            if data.startswith('ask:'):
                package = data[4:]
                if package in PACKAGES:
                    try:
                        fresh = self.store.request(uid, package)
                        self.send(uid, 'Заявка отправлена. / Request sent.' if fresh else 'Заявка уже отправлена. / Request already sent.')
                    except PermissionError:
                        self.send(uid, 'Доступ ограничен владельцем. / Access restricted by the owner.')
                return
            if uid not in self.admins:
                self.send(uid, 'Эта команда доступна только владельцу. / Owner only.')
                return
            parts = data.split(':')
            try:
                if data == 'home':
                    self.home(uid)
                elif data == 'help':
                    self.send(uid, 'Ученик должен открыть бота или приложение. Затем выберите его в списке или отправьте /user ЧИСЛОВОЙ_ID.\nДля смены прав самого владельца используйте ADMIN_IDS в настройках сервера.')
                elif parts[0] in {'users','requests'} and len(parts) == 2:
                    self.show_list(uid, parts[0], int(parts[1]))
                elif parts[0] == 'user' and len(parts) == 2:
                    self.show_user(uid, int(parts[1]))
                elif parts[0] == 'pick' and len(parts) == 3 and parts[2] in PACKAGES:
                    target = int(parts[1])
                    if self.store.user(target):
                        self.send(uid, f'Открыть {PACKAGES[parts[2]][0]} для {target}?',[ [button('На 30 дней', f'grant:{target}:{parts[2]}:30')], [button('Бессрочно', f'grant:{target}:{parts[2]}:forever')], [button('Назад', f'user:{target}')]])
                elif parts[0] == 'grant' and len(parts) == 4 and parts[3] in {'30','forever'}:
                    target = int(parts[1])
                    if target in self.admins:
                        self.send(uid, 'Владелец имеет полный доступ по ADMIN_IDS. Управляйте учениками из списка.')
                        return
                    expires = int(time.time()) + 30 * 86400 if parts[3] == '30' else None
                    self.store.mutate(uid, target, 'grant', parts[2], expires, update['update_id'])
                    self.show_user(uid, target)
                elif parts[0] == 'revoke' and len(parts) == 3:
                    self.store.mutate(uid, int(parts[1]), 'revoke', parts[2], update_id=update['update_id'])
                    self.show_user(uid, int(parts[1]))
                elif parts[0] in {'block','unblock'} and len(parts) == 2:
                    target = int(parts[1])
                    if target in self.admins:
                        self.send(uid, 'Владельца нельзя заблокировать кнопкой. Измените ADMIN_IDS на сервере.')
                        return
                    self.store.mutate(uid, target, parts[0], update_id=update['update_id'])
                    self.show_user(uid, target)
            except (ValueError, OverflowError):
                self.send(uid, 'Неизвестная команда или ученик. / Unknown command or user.')
            return
        text = str(message.get('text', ''))
        command = text.split(maxsplit=1)[0].split('@')[0] if text else ''
        if command == '/id':
            self.send(uid, f'Telegram ID: {uid}')
        elif command in {'/start','/help'}:
            self.user_home(user)
        elif command in {'/support','/paysupport','/terms'}:
            self.send(uid, self.support_text)
        elif command == '/request':
            self.request_menu(uid)
        elif command == '/admin' and uid in self.admins:
            self.home(uid)
        elif command == '/user' and uid in self.admins:
            try:
                self.show_user(uid, int(text.split()[1]))
            except (IndexError, ValueError):
                self.send(uid, 'Формат: /user ЧИСЛОВОЙ_ID')
        else:
            self.user_home(user)

    def run(self):
        # A pre-existing webhook is never deleted automatically.
        while not self.stop.is_set():
            try:
                values = self.api.call('getUpdates', offset=self.store.offset(), timeout=25, allowed_updates=['message','callback_query'])
                for update in values:
                    self.handle(update)
                    self.store.set_offset(update['update_id'] + 1)
            except Exception:
                log.warning('Bot polling failed. Check token, network and existing webhook; no permissions were relaxed.')
                self.stop.wait(5)
