"""Exam grading structure and server-side entitlement boundaries."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from access_store import AccessStore
from server import Application, Config
from test_access import TOKEN, ADMIN, STUDENT, signed, FakeAPI


class ExamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AccessStore(Path(self.tmp.name) / 'exam.sqlite3')
        self.store.register(dict(id=STUDENT, first_name='Student'))
        self.app = Application(Config(TOKEN, frozenset({ADMIN}), 'https://example.test', self.store.path), store=self.store, api=FakeAPI())

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, auth=None, shell=False):
        result = {}
        env = dict(PATH_INFO='/' if shell else '/api/bootstrap', REQUEST_METHOD='GET' if shell else 'POST', CONTENT_TYPE='application/json', CONTENT_LENGTH='2')
        env['wsgi.input'] = io.BytesIO(b'{}')
        if auth:
            env['HTTP_AUTHORIZATION'] = 'tma ' + auth
        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        result['body'] = b''.join(self.app(env, start))
        return result if shell else json.loads(result['body'])

    def test_all_eight_sections_match_paper_totals(self):
        exam = self.get(signed(ADMIN))['exam']
        self.assertEqual(len(exam['questions']), 61)
        self.assertEqual(len({q['id'] for q in exam['questions']}), 61)
        self.assertEqual([sum(q['points'] for q in exam['questions'] if q['section'] == str(n)) for n in range(1, 9)], [10,10,10,10,15,10,20,15])
        self.assertEqual(sum(q['points'] for q in exam['questions'] if q['type'] in {'manual','essay'}), 35)
        self.assertEqual(len([q for q in exam['questions'] if q.get('image')]), 10)
        for q in exam['questions']:
            self.assertTrue(q['note']['ru'])
            self.assertTrue(q['note']['en'])

    def test_handwritten_errors_are_not_the_answer_key(self):
        by_id = {q['id']:q for q in self.get(signed(ADMIN))['exam']['questions']}
        self.assertEqual(by_id['4-1']['answer'], ['碗'])
        self.assertEqual(by_id['4-4']['answer'], ['把'])
        self.assertEqual(by_id['4-9']['answer'], ['我的'])
        self.assertEqual(by_id['7-1']['answer'], ['false'])
        self.assertEqual(by_id['7-3']['answer'], ['true'])
        self.assertEqual(by_id['3-2']['answer'], ['9'])
        self.assertEqual(by_id['3-7']['answer'], ['3'])
        self.assertEqual(by_id['3-10']['answer'], ['2'])
        self.assertEqual(by_id['1-4']['target'], '电话')

    def test_trial_and_partial_packages_never_receive_exam(self):
        self.assertIsNone(self.get()['exam'])
        for package in ['book8', 'hsk1', 'hsk2']:
            self.store.mutate(ADMIN, STUDENT, 'grant', package)
            payload = self.get(signed())
            self.assertIsNone(payload['exam'])
            self.assertNotIn('我要换六百人民币的欧元', json.dumps(payload, ensure_ascii=False))
            self.store.mutate(ADMIN, STUDENT, 'revoke', package)

    def test_grant_revoke_and_block_cover_exam(self):
        for package in ['book16', 'all']:
            self.store.mutate(ADMIN, STUDENT, 'grant', package)
            self.assertIsNotNone(self.get(signed())['exam'])
            self.store.mutate(ADMIN, STUDENT, 'block')
            self.assertIsNone(self.get(signed())['exam'])
            self.store.mutate(ADMIN, STUDENT, 'unblock')
            self.assertIsNotNone(self.get(signed())['exam'])
            self.store.mutate(ADMIN, STUDENT, 'revoke', package)
            self.assertIsNone(self.get(signed())['exam'])

    def test_shell_contains_no_exam_content_or_images(self):
        response = self.get(shell=True)
        html = response['body'].decode()
        self.assertIn('const EXAM_DATA=BOOT.exam;', html)
        self.assertNotIn('我要换六百人民币的欧元', html)
        self.assertNotIn('data:image/jpeg;base64,', html)
        self.assertIn("img-src 'self' data:", response['headers']['Content-Security-Policy'])
