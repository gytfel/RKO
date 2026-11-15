"""Opt-in Telegram notifications to the owner's own bot/chat only.
Configured by root in /etc/rko-radar-notify.env; no private messaging or outreach.
"""
import os
import requests


def configured():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat = os.getenv('TELEGRAM_CHAT_ID', '')
    return bool(token and chat)


def send_message(text):
    if not configured():
        return False, 'Уведомления не настроены'
    try:
        response = requests.post(
            'https://api.telegram.org/bot' + os.environ['TELEGRAM_BOT_TOKEN'] + '/sendMessage',
            data={'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': text[:3500], 'disable_web_page_preview': True},
            timeout=(5, 10),
        )
        response.raise_for_status()
        payload = response.json()
        return bool(payload.get('ok')), ('Отправлено' if payload.get('ok') else 'Telegram отклонил запрос')
    except (requests.RequestException, ValueError) as e:
        # Do not log the actual URL: it contains the bot token.
        return False, f'Ошибка подключения к Telegram ({type(e).__name__})'


def new_opportunities(items):
    if not configured(): return
    for item in items[:5]:
        text = ('🔎 RKO Radar: возможное обращение\n\n' + item['title'][:240] +
                '\nОценка: ' + str(item['score']) + '/100\n' + item['url'][:1500] +
                '\n\nПроверьте контекст перед ответом. Это не подтверждённая заявка.')
        send_message(text)
