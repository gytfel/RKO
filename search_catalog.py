"""Topic-balanced search plans over public indexed sites.

Neither search engine scans the entire internet. Domain queries only suggest
public pages already indexed by the provider. No scraping, private data or outreach.
"""
RKO_QUERIES = (
    '"где открыть расчетный счет" "подскажите" ИП',
    '"посоветуйте РКО" для ИП',
    '"ищу банк для ИП" обсуждение',
    '"какой банк выбрать для ООО" форум',
    'site:hranidengi.com/forums/banki-dlya-biznesa/ "посоветуйте"',
    'site:banki.ru "открыть расчетный счет" "подскажите"',
    'site:vc.ru "РКО" "посоветуйте" ИП',
    'site:qna.habr.com "расчетный счет" "банк"',
    'site:pikabu.ru "какой банк выбрать" ИП',
    '"нужен расчетный счет для ИП" "банк"',
)
CARD_QUERIES = (
    '"какую дебетовую карту выбрать" "подскажите" форум',
    '"хочу оформить дебетовую карту" "посоветуйте"',
    '"ищу дебетовую карту" "кешбэк" форум',
    '"где оформить дебетовку" "посоветуйте"',
    '"хочу завести новую карту" "посоветуйте"',
    'site:hranidengi.com/forums/debetovye-karty/ "подскажите" "выбрать"',
    'site:hranidengi.com "подскажите дебетовку" "целей"',
    'site:banki.ru "какую дебетовую карту" "выбрать"',
    'site:vc.ru "нужна дебетовая карта" "совет"',
    'site:pikabu.ru "посоветуйте дебетовую карту"',
    'site:otvet.mail.ru "какую дебетовую карту оформить"',
    '"подскажите карту для покупок" "бесплатное обслуживание"',
    '"какую карту заказать" "кешбэк" форум',
    '"ищу карту без платы за обслуживание" "посоветуйте"',
)
REFERRAL_QUERIES = (
    '"ищу реферальную ссылку" "дебетовая карта"',
    '"нужна реферальная ссылка" "Альфа-Карта"',
    '"у кого есть приглашение" "Альфа-Банк" "карта"',
    '"поделитесь приглашением" "дебетовая карта"',
    '"хочу оформить карту по приглашению" банк',
    '"ищу рефку" "дебетовая карта"',
    '"есть рефералка" "Альфа" "карта" вопрос',
    'site:hranidengi.com "ищу приглашение" "Альфа"',
    'site:hranidengi.com "нужна реферальная ссылка" "карта"',
    'site:banki.ru "ищу реферальную ссылку" "карта"',
    'site:vc.ru "нужна реферальная ссылка" "банк"',
    '"готов оформить дебетовую карту по ссылке"',
)


def tavily_plan(cursor=0):
    """5 calls: 2 RKO, 2 debit cards, 1 explicit referral question."""
    c = int(cursor)
    return [
        RKO_QUERIES[c % len(RKO_QUERIES)],
        CARD_QUERIES[c % len(CARD_QUERIES)],
        REFERRAL_QUERIES[c % len(REFERRAL_QUERIES)],
        CARD_QUERIES[(c + 1) % len(CARD_QUERIES)],
        RKO_QUERIES[(c + 1) % len(RKO_QUERIES)],
    ]


def deepseek_plan(cursor=0):
    """3 calls: 1 RKO, 1 debit card, 1 referral question."""
    c = int(cursor)
    return [
        RKO_QUERIES[c % len(RKO_QUERIES)],
        CARD_QUERIES[c % len(CARD_QUERIES)],
        REFERRAL_QUERIES[c % len(REFERRAL_QUERIES)],
    ]


PUBLIC_SITE_NAMES = ("Храни Деньги", "Банки.ру", "VC.ru", "Пикабу", "Хабр Q&A", "Ответы Mail.ru")
