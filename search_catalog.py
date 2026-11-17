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
    'вопрос открыть расчетный счет ИП форум сегодня',
    'посоветуйте банк для бизнеса новый пост',
    'site:hranidengi.com/threads/ открытие расчетного счета ответ',
    'site:banki.ru/dialog/ расчетный счет вопрос ИП',
    'site:otvet.mail.ru открыть расчетный счет для ИП',
    'site:bankiros.ru/questions/ расчетный счет открыть ИП',
    'site:bankiros.ru/questions "банк для ИП" открыть',
    'site:banki.ru/services/questions-answers/question/ открыть расчетный счет',
    'site:banki.ru/dialog/ "посоветуйте РКО"',
    'site:banki.ru/services/questions-answers/ банк для бизнеса',
)
CARD_QUERIES = (
    'посоветуйте дебетовую карту для покупок',
    'какую дебетовую карту выбрать кешбэк',
    'хочу оформить дебетовую карту какой банк',
    'нужна дебетовка с бесплатным обслуживанием',
    'подскажите карту для покупок и переводов',
    'site:hranidengi.com/threads/podskazhite-debetovku-dlja-opredelennyx-celej.25/ дебетовка посоветуйте',
    'site:hranidengi.com "подскажите дебетовку"',
    'site:banki.ru "дебетовая карта" "посоветуйте"',
    'site:vc.ru какую карту выбрать кешбэк',
    'site:pikabu.ru посоветуйте дебетовку',
    'site:otvet.mail.ru посоветуйте карту банк',
    '"подскажите карту для покупок" "бесплатное обслуживание"',
    '"какую карту заказать" "кешбэк" форум',
    '"ищу карту без платы за обслуживание" "посоветуйте"',
    'посоветуйте дебетовку свежие ответы',
    'какую дебетовку открыть вопрос форум сегодня',
    'site:hranidengi.com/threads/ дебетовая карта посоветуйте',
    'site:banki.ru/dialog/ какую карту открыть',
    'site:qna.habr.com дебетовая карта выбрать банк',
    'site:bankiros.ru/questions/ "дебетовую карту" выбрать',
    'site:bankiros.ru/questions дебетовые карты посоветуйте',
    'site:banki.ru/services/questions-answers/question/ "оформить дебетовую карту"',
    'site:banki.ru/dialog/ "какую дебетовую карту выбрать"',
)
REFERRAL_QUERIES = (
    'ищу реферальную ссылку дебетовая карта',
    'нужна рефералка на Альфа карту',
    'у кого есть приглашение на Альфа карту',
    'поделитесь ссылкой приглашением банк карта',
    'оформить дебетовую карту по приглашению',
    '"ищу рефку" "дебетовая карта"',
    '"есть рефералка" "Альфа" "карта" вопрос',
    'site:hranidengi.com "ищу приглашение" Альфа',
    'site:hranidengi.com реферальная ссылка карта нужна',
    'site:banki.ru реферальная ссылка карта ищу',
    'site:vc.ru пригласительная ссылка дебетовая карта',
    '"готов оформить дебетовую карту по ссылке"',
    'кто поделится приглашением на альфа карту',
    'нужна рефералка дебетовая карта пост',
    'site:hranidengi.com/threads/ нужна реферальная ссылка',
    'site:bankiros.ru/questions приглашение банковская карта',
    'site:banki.ru/services/questions-answers/question/ реферальная ссылка',
    'site:banki.ru/dialog/ ищу реферальную ссылку',
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


PUBLIC_SITE_NAMES = ("Храни Деньги", "Банки.ру", "Bankiros.ru", "VC.ru", "Пикабу", "Хабр Q&A", "Ответы Mail.ru")
