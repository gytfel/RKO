"""Local explainable relevance scoring (not a proof of buying intent).
Only uses text of published RSS entries. No private identities or AI APIs.
"""
from html import unescape
import re

TOPICS = {
    'РКО': [r'\bрко\b', r'расч[её]тн\w*\s+сч[её]т', r'сч[её]т\s+(?:для|на)\s+(?:ип|ооо|бизнеса)', r'банк\s+для\s+(?:ип|ооо|бизнеса)', r'банк\w*\s+(?:выбрать|лучше|посоветуете)\s+для\s+(?:ип|ооо)', r'сч[её]т\s+(?:ип|ооо)', r'расч[её]тно.кассов'],
    'Регистрация бизнеса': [r'откры\w*\s+ип\b', r'регистрац\w*\s+ип', r'зарегистрирова\w*\s+ооо', r'откры\w*\s+ооо', r'оформить\s+ип', r'регистрир\w*\s+бизнес'],
    'Маркетплейсы': [r'wildberries', r'вайлдберриз', r'\bozon\b', r'\bозон\b', r'маркетплейс', r'селлер', r'продавц\w*\s+на\s+(?:вб|озон)'],
    'Карты': [r'дебетов\w*\s+карт', r'к[еэ]шб[еэ]к', r'банковск\w*\s+карт', r'карт\w*\s+для\s+покупок'],
}
STRONG_INTENT = [
    r'\b(?:мне|нам)\s+(?:нужен|нужна|нужно|требуется)\b',
    r'\b(?:хочу|планирую|собираюсь|собираемся|ищу|выбираю|подыскиваю|думаю)\b',
    r'\b(?:посоветуйте|подскажите|порекомендуйте|помогите\s+выбрать)\b',
    r'\b(?:какой|какую)\s+банк\w*\s+\w*',
    r'\bгде\s+(?:лучше|можно|выгоднее)?\s*(?:открыть|оформить|зарегистрировать)\b',
    r'\bкто\s+(?:пользуется|открывал|рекомендует)\b',
]
QUESTION = [r'\?', r'\b(?:подскажите|посоветуйте|помогите|какой\s+банк|где\s+открыть|что\s+выбрать)\b']
ARTICLES = [
    r'\b(?:новост\w*|пресс.релиз\w*|отч[её]т\w*|исследован\w*|аналитик\w*|топ.\d+|рейтинг\w*)\b',
    r'\b(?:банк\w*\s+(?:сообщил|объявил|заявил|запустил|представил|рассказал))\b',
    r'\b(?:реклама|спонсор|партн[её]рск\w*\s+материал|рекламодатель)\b',
    r'\b(?:инструкци\w*|обзор\w*|дайджест\w*|подборк\w*)\b',
]
HOWTO_TITLE = r'^(?:как|где|какой|что)\s+.{5,100}\s*[:—–-]\s*(?:пошагов|инструкц|обзор|список|советы)'

def matches(patterns, text):
    return sum(1 for p in patterns if re.search(p, text, re.I))

def score_text(title: str, description: str = '') -> dict:
    heading = unescape(title or '').lower()[:300]
    body = unescape(description or '').lower()[:2500]
    text = heading + ' ' + body
    counts = {name: matches(patterns, text) for name, patterns in TOPICS.items()}
    # Prioritize RKO/registration when combined with a marketplace or generic card context.
    category = next((c for c in ('РКО','Регистрация бизнеса') if counts[c] > 0), None)
    if category is None:
        category = max(counts, key=counts.get)
    if counts[category] == 0:
        return {'category':'Другое','score':0,'reason':'Нет признаков нужной темы', 'intent_label':'information','intent_summary':'Нерелевантная публикация'}
    strong = matches(STRONG_INTENT, text)
    question = bool(matches(QUESTION, heading))
    article = matches(ARTICLES, text)
    howto = bool(re.search(HOWTO_TITLE, heading))
    # Without a direct request, publication is not an actionable potential customer.
    direct = bool(strong and (question or strong >= 2))
    if article or howto:
        direct = False
    # Marketplace posts alone aren't demand for a business bank account.
    business_product = category in ('РКО','Регистрация бизнеса')
    qualified = direct and business_product
    score = 14 + min(22, counts[category]*11) + min(32, strong*16) + (20 if question else 0) - min(45, article*20) - (25 if howto else 0)
    if not business_product: score -= 20
    if not direct: score = min(score, 49)
    if not qualified: score = min(score, 59)
    score = max(0, min(100, score))
    if qualified:
        intent_label = 'potential'
        intent_summary = 'Есть признаки личного вопроса о банковском продукте или регистрации бизнеса'
    elif article or howto:
        intent_label = 'information'
        intent_summary = 'Скорее статья, новость или рекламный материал'
    else:
        intent_label = 'possible'
        intent_summary = 'Тема подходит, но явного намерения оформить продукт не найдено'
    reasons = [f'Тема: {category}', intent_summary]
    return {'category':category, 'score':score, 'reason':'; '.join(reasons), 'intent_label':intent_label, 'intent_summary':intent_summary}
