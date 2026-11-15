#!/usr/bin/env python3
"""Safely configure RKO Radar's opt-in personal Telegram alerts.
Run as root through `rko-radar telegram setup`.
"""
import getpass
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

CFG = '/etc/rko-radar-notify.env'


def telegram(token, method, data=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    post = urlencode(data).encode() if data else None
    with urlopen(url, data=post, timeout=12) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram не подтвердил операцию')
    return result['result']


def main():
    if os.geteuid() != 0:
        raise SystemExit('Нужно запустить от root')
    print('=== Подключение Telegram к RKO Radar ===')
    print('1. Создай бота через @BotFather в Telegram.')
    print('2. Скопируй его токен. Токен не будет показан в терминале.')
    token = getpass.getpass('Токен бота: ').strip()
    if not re.fullmatch(r'[0-9]{5,12}:[A-Za-z0-9_-]{20,100}', token):
        raise SystemExit('Неверный формат токена')
    try:
        bot = telegram(token, 'getMe')
        print('Найден бот: @' + bot.get('username','без имени'))
        print('3. Открой его в Telegram и отправь /start с личного аккаунта.')
        input('После отправки нажми Enter здесь... ')
        updates = telegram(token, 'getUpdates', {'limit': 100, 'timeout': 0})
        private = [u['message']['chat'] for u in updates if 'message' in u and u['message'].get('chat',{}).get('type')=='private']
        if private:
            chat = private[-1]['id']
            print('Личный чат обнаружен автоматически.')
        else:
            chat = input('Новых сообщений не найдено. Введи свой Telegram Chat ID: ').strip()
            if not re.fullmatch(r'[0-9]{4,18}', str(chat)):
                raise SystemExit('Некорректный Chat ID; повтори настройку')
        telegram(token, 'sendMessage', {'chat_id': str(chat), 'text': '✅ RKO Radar: Telegram подключён.'})
    except (URLError, HTTPError, TimeoutError, ValueError, RuntimeError, json.JSONDecodeError) as ex:
        raise SystemExit('Не удалось связаться с Telegram. Проверь токен, /start и доступ к api.telegram.org. Тип ошибки: '+type(ex).__name__)
    with open(CFG, 'w', encoding='utf8') as f:
        f.write('TELEGRAM_BOT_TOKEN=' + token + '\nTELEGRAM_CHAT_ID=' + str(chat) + '\n')
    os.chmod(CFG, 0o600)
    print('✓ Настройки сохранены. Теперь служба должна перезапуститься.')


if __name__=='__main__':
    main()
