"""Integration tests; run after installing requirements: python -m unittest discover -s tests"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['DATA_DIR'] = cls.tmp.name
        os.environ['ADMIN_PASSWORD'] = 'test-password-strong-enough'
        os.environ['APP_SECRET_KEY'] = 'test-secret-key-over-32-characters-is-used'
        os.environ['DISABLE_SCHEDULER'] = '1'
        import app
        cls.application = app.app

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.client = self.application.test_client()

    def auth(self):
        self.client.get('/login')
        with self.client.session_transaction() as s:
            token = s['csrf']
        response = self.client.post('/login', data={'password': 'test-password-strong-enough', 'csrf': token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as s:
            return s['csrf']

    def test_auth_and_csrf(self):
        self.assertEqual(self.client.get('/api/stats').status_code, 401)
        token = self.auth()
        self.assertEqual(self.client.get('/api/stats').status_code, 200)
        self.assertEqual(self.client.post('/api/manual', json={}).status_code, 403)
        self.assertTrue(token)

    def test_remember_device(self):
        self.client.get('/login')
        with self.client.session_transaction() as s:
            token = s['csrf']
        response = self.client.post('/login', data={'csrf': token, 'password': 'test-password-strong-enough', 'remember_device': 'yes'})
        self.assertEqual(response.status_code, 302)
        cookie = response.headers.get('Set-Cookie', '')
        self.assertIn('Expires=', cookie)  # Flask sets Expires for permanent sessions
        self.assertEqual(self.client.get('/api/stats').status_code, 200)

    def test_login_without_remembering(self):
        self.client.get('/login')
        with self.client.session_transaction() as s:
            token = s['csrf']
        response = self.client.post('/login', data={'csrf': token, 'password': 'test-password-strong-enough'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('Max-Age=', response.headers.get('Set-Cookie', ''))
        self.assertEqual(self.client.get('/api/stats').status_code, 200)

    def test_lead_flow(self):
        token = self.auth()
        h = {'X-CSRF-Token': token}
        created = self.client.post('/api/manual', json={'title': 'Как открыть расчётный счёт ИП?', 'url': 'https://example.com/discussion/rko', 'description': 'Хочу выбрать РКО'}, headers=h)
        self.assertIn(created.status_code, (201, 400)) # duplicate when tests share db
        rows = self.client.get('/api/leads?min_score=0').get_json()['items']
        lead_id = next(r['id'] for r in rows if r['url']=='https://example.com/discussion/rko')
        self.assertEqual(self.client.patch(f'/api/leads/{lead_id}', json={'stage':'review', 'notes':'Проверить обсуждение'}, headers=h).status_code, 200)
        self.assertEqual(self.client.get('/api/export?min_score=0').status_code, 200)


    def test_referral_card_filter_and_stats(self):
        token = self.auth()
        h = {'X-CSRF-Token':token}
        example = {'title': 'Ищу реферальную ссылку на дебетовую карту Альфа-Банка',
                   'url': 'https://example.com/discussion/referral-applicant-2026',
                   'description': 'Хочу оформить карту по приглашению, подскажите',
                   'published_at': datetime.now(timezone.utc).date().isoformat()}
        response = self.client.post('/api/manual', json=example, headers=h)
        self.assertEqual(response.status_code, 201)
        stats = self.client.get('/api/stats').get_json()
        self.assertGreaterEqual(stats['card_requests'], 1)
        self.assertGreaterEqual(stats['referral_requests'], 1)
        rows = self.client.get('/api/leads?only_potential=1&category=Карты&referral_only=1&min_score=0').get_json()['items']
        self.assertTrue(any(x['url']==example['url'] and x['referral_signal']=='asked' for x in rows))

    def test_card_review_signals_are_not_confused_with_leads(self):
        token = self.auth()
        example = {'title': 'Дебетовая карта для покупок и кешбэка',
                   'url': 'https://example.com/discussion/card-review-nonlead-2026',
                   'description': 'Обсуждение полезных тарифов без явного запроса оформить карту',
                   'published_at': datetime.now(timezone.utc).date().isoformat()}
        response = self.client.post('/api/manual', json=example, headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code, 201)
        signal_rows = self.client.get('/api/leads?category=Карты&signals_only=1&min_score=0').get_json()['items']
        opportunity_rows = self.client.get('/api/leads?category=Карты&only_potential=1&min_score=0').get_json()['items']
        self.assertTrue(any(x['url']==example['url'] for x in signal_rows))
        self.assertFalse(any(x['url']==example['url'] for x in opportunity_rows))


    def test_old_and_undated_manual_posts_only_in_archive(self):
        token = self.auth()
        h = {'X-CSRF-Token': token}
        for suffix, pub in [('old', '2022-03-10'), ('undated', '')]:
            item = {'title': 'Подскажите, какую дебетовую карту выбрать?',
                    'description': 'Хочу оформить дебетовую карту',
                    'url': 'https://example.com/discussion/date-test-'+suffix,
                    'published_at': pub}
            response = self.client.post('/api/manual', json=item, headers=h)
            self.assertEqual(response.status_code, 201)
            archive = self.client.get('/api/leads?min_score=0').get_json()['items']
            self.assertTrue(any(x['url'] == item['url'] for x in archive))
            fresh = self.client.get('/api/leads?only_potential=1&min_score=0').get_json()['items']
            self.assertFalse(any(x['url'] == item['url'] for x in fresh))
            signal = self.client.get('/api/leads?signals_only=1&category=Карты&min_score=0').get_json()['items']
            self.assertFalse(any(x['url'] == item['url'] for x in signal))

    def test_prohibited_internal_source(self):
        token = self.auth()
        r = self.client.post('/api/sources', json={'name':'Localhost', 'url':'https://127.0.0.1/'}, headers={'X-CSRF-Token':token})
        self.assertEqual(r.status_code, 400)

if __name__=='__main__': unittest.main()
