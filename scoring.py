"""Local explainable relevance scoring (not a proof of buying intent).
Only uses text of published RSS entries. No private identities or AI APIs.
"""
from html import unescape
import re

TOPICS = {
    'РКО': [r'\bрко\b', r'расч[её]тн\w*\s+сч[её]т', r'сч[её]т\s+(?:для|на)\s+(?:ип|ооо|бизнеса)', r'банк\s+для\s+(?:ип|ооо|бизнеса)', r'банк\w*\s+(?:выбрать|лучше|посоветуете)\s+для\s+(?:ип|ооо)', r'сч[её]т\s+(?:ип|ооо)', r'расч[её]тно.кассов'],
    'Регистрация бизнеса': [r'откры\w*\s+ип\b', r'регистрац\w*\s+ип', r'зарегистрирова\w*\s+ооо', r'откры\w*\s+ооо', r'оформить\s+ип', r'регистрир\w*\s+бизнес'],
    'Маркетплейсы': [r'wildberries', r'вайлдберриз', r'\bozon\b', r'\bозон\b', r'маркетплейс', r'селлер', r'продавц\w*\s+на\s+(?:вб|озон)'],
    'Карты': [r'дебетов\w*\s+карт', r'к[еэ]шб[еэ]к', r'банковск\w*\s+карт', r'карт\w*\s+для\s+покупок',
              r'\bдебетовк\w*\b', r'\b(?:оформить|заказать|завести|выбрать)\s+(?:себе\s+|новую\s+|дебетовую\s+|банковскую\s+)*карт\w*',
              r'\b(?:реферальн\w*\s+ссылк\w*|приглашени\w*)\s+(?:на|для)\s+дебетов\w*\s+карт\w*',
              r'\b(?:альфа.карта|карта\s+альфа.банка)\b'],
}
STRONG_INTENT = [
    r'\b(?:мне|нам)\s+(?:нужен|нужна|нужно|требуется)\b',
    r'\b(?:хочу|планирую|собираюсь|собираемся|ищу|выбираю|подыскиваю|думаю)\b',
    r'\b(?:посоветуйте|подскажите|порекомендуйте|помогите\s+выбрать)\b',
    r'\b(?:какой|какую)\s+банк\w*\s+\w*',
    r'\b(?:какую|какая|какой)\s+(?:дебетов\w*\s+|банковск\w*\s+)?карт\w*\b',
    r'\b(?:нужен|нужна|нужно|требуется)\s+(?:мне\s+|нам\s+)?(?:карт\w*|сч[её]т|рко)\b',
    r'\b(?:посоветуй|посоветуйте|подскажи|подскажите)\b',
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
# Indexed results can include SEO articles and old forum threads.
EDITORIAL_TITLE = [
    r'^\s*\d{1,2}\s+(?:банков|банка|сервисов|способов|лучших|вариантов)\b',
    r'\b(?:топ[- ]?\d+|рейтинг|сравнение|обзор|подборка)\s+(?:банков|рко|счетов|карт|тарифов)\b',
    r'^\s*(?:какой|какие|как|где|что)\s+[^?]{12,140}[:—–-]\s*[^?]{12,140}$',
]
PREMIUM_SERVICE = r'\b(?:премиум|premium|премиальн\w*\s+(?:пакет|подписк)|привилеги\w*|продлени\w*\s+премиум)\b'
CARD_ACQUISITION = r'\b(?:открыть|оформить|выбрать|посоветуйте|подскажите)\s+(?:мне\s+|новую\s+|лучшую\s+|дебетовую\s+|банковскую\s+)*карт\w*\b'


# "Interested in a referral" is only an observable PUBLIC request, not consent.
REFERRAL_REQUEST = [
    r'\b(?:ищу|нужна|нужен|поделитесь|скиньте|дайте)\b.{0,45}\b(?:реф(?:еральн\w*)?\s*ссылк\w*|рефк\w*|приглашени\w*|рефералк\w*)\b',
    r'\b(?:у кого|есть у кого|кто даст)\s+.{0,30}\b(?:приглашени\w*|рефералк\w*|рефк\w*|реферальн\w*\s+ссылк\w*)\b',
    r'\b(?:хочу|готов|собираюсь)\s+оформить\b.{0,55}\b(?:по приглашению|по реферальной ссылке)\b',
]
REFERRAL_AD = [
    r'\b(?:по моей|моя)\s+(?:реферальной\s+)?ссылк\w*',
    r'\b(?:приглашаю|дарю|предлагаю|раздаю)\b.{0,45}\b(?:ссылк\w*|карт\w*|бонус\w*)',
    r'\b(?:переходите по ссылке|оформите по ссылке|получите бонус|получи бонус|рефбэк от меня)\b',
    r'https?://\S+\b(?:ref|invite|promo)\b',
]
CREDIT_ONLY = r'\b(?:кредитн\w*\s+карт\w*|кредитк\w*|кредитный лимит|минимальный платеж)\b'

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
        return {'category':'Другое','score':0,'reason':'Нет признаков нужной темы', 'intent_label':'information','intent_summary':'Нерелевантная публикация', 'referral_signal':''}
    strong = matches(STRONG_INTENT, text)
    # Some RSS feeds include the actual question only in the summary.
    question = bool(matches(QUESTION, heading)) or ("?" in body and strong > 0)
    advertised = bool(matches(REFERRAL_AD, heading + ' ' + body[:600]))
    referral_request = (category == 'Карты' and bool(matches(REFERRAL_REQUEST, heading + ' ' + body[:400]))
                        and not advertised and not re.search(CREDIT_ONLY, heading))
    article = matches(ARTICLES, text)
    howto = bool(re.search(HOWTO_TITLE, heading))
    editorial = bool(matches(EDITORIAL_TITLE, heading)) or bool(re.search(r'\|[^\n]{5,100}\|[^\n]{5,100}\|', body))
    # A question in a quoted forum reply or an SEO table is not a new
    # application for a debit card (e.g. extending a premium service).
    premium_maintenance = (category == 'Карты'
                           and bool(re.search(PREMIUM_SERVICE, heading))
                           and not bool(re.search(CARD_ACQUISITION, heading)))
    # Without a direct request, publication is not an actionable potential customer.
    personal_request = bool(re.search(
        r'\b(?:хочу|планирую|собираюсь|собираемся|ищу|выбираю|нужен|нужна|нужно|требуется)\b',
        heading
    ))
    direct = bool(strong and (question or strong >= 2 or personal_request))
    if article or howto or editorial or premium_maintenance or advertised:
        direct = False
    if referral_request and not (article or howto or editorial or premium_maintenance):
        direct = True
    # A specific debit-card question can be a lead too. Generic marketplace
    # mentions and banking news alone are NOT customer requests.
    financial_product = category in ('РКО','Регистрация бизнеса','Карты')
    qualified = direct and financial_product
    score = 14 + min(22, counts[category]*11) + min(32, strong*16) + (20 if question else 0) - min(45, article*20) - (25 if howto else 0)
    if not financial_product: score -= 20
    if editorial or premium_maintenance or advertised: score = min(score, 25)
    if referral_request and qualified: score = max(score, 85)
    if not direct: score = min(score, 49)
    if not qualified: score = min(score, 59)
    score = max(0, min(100, score))
    if qualified:
        intent_label = 'potential'
        intent_summary = 'Возможно, человек лично выбирает банковский продукт или регистрацию бизнеса'
    elif article or howto or editorial or premium_maintenance or advertised:
        intent_label = 'information'
        intent_summary = 'Скорее статья, новость или рекламный материал'
    else:
        intent_label = 'possible'
        intent_summary = 'Тема подходит, но явного намерения оформить продукт не найдено'
    referral_signal = 'asked' if referral_request and qualified else ''
    if referral_signal:
        intent_summary = 'Публичный вопрос о реферальной ссылке/приглашении; заинтересованность нужно проверить'
    reasons = [f'Тема: {category}', intent_summary]
    return {'category':category, 'score':score, 'reason':'; '.join(reasons), 'intent_label':intent_label, 'intent_summary':intent_summary, 'referral_signal':referral_signal}
