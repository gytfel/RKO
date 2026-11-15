"""Integration tests; run after installing requirements: python -m unittest discover -s tests"""
import os
import tempfile
import unittest

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

    def test_lead_flow(self):
        token = self.auth()
        h = {'X-CSRF-Token': token}
        created = self.client.post('/api/manual', json={'title': 'Как открыть расчётный счёт ИП?', 'url': 'https://example.com/discussion/rko', 'description': 'Хочу выбрать РКО'}, headers=h)
        self.assertIn(created.status_code, (201, 400)) # duplicate when tests share db
        rows = self.client.get('/api/leads?min_score=0').get_json()['items']
        lead_id = next(r['id'] for r in rows if r['url']=='https://example.com/discussion/rko')
        self.assertEqual(self.client.patch(f'/api/leads/{lead_id}', json={'stage':'review', 'notes':'Проверить обсуждение'}, headers=h).status_code, 200)
        self.assertEqual(self.client.get('/api/export?min_score=0').status_code, 200)

    def test_prohibited_internal_source(self):
        token = self.auth()
        r = self.client.post('/api/sources', json={'name':'Localhost', 'url':'https://127.0.0.1/'}, headers={'X-CSRF-Token':token})
        self.assertEqual(r.status_code, 400)

if __name__=='__main__': unittest.main()
