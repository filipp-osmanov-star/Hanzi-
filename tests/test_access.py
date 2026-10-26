import hashlib
import hmac
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from access_store import AccessStore
from bot import AdminBot
from server import Application, Config, CourseRepository, validate_init_data

TOKEN = '123456:test_token_for_local_tests'
ADMIN, STUDENT = 1001, 2002
ROOT = Path(__file__).resolve().parents[1]


def signed(user_id=STUDENT, date=None, **extra):
    fields = dict(auth_date=str(int(time.time()) if date is None else date), user=json.dumps(dict(id=user_id, first_name='Student'), separators=(',',':')), **extra)
    secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeAPI:
    def __init__(self): self.calls=[]
    def call(self, method, **data): self.calls.append((method,data)); return {'message_id':1}


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=AccessStore(Path(self.temp.name)/'access.sqlite3')
        self.store.register(dict(id=STUDENT, first_name='Student'))
        self.api=FakeAPI()
        self.app=Application(Config(TOKEN,frozenset({ADMIN}),'https://example.test',self.store.path), store=self.store,api=self.api)

    def tearDown(self): self.temp.cleanup()

    def request(self, path='/api/bootstrap', auth=None, body=None, method='POST', origin=None):
        raw=json.dumps(body or {}).encode(); result={}
        env=dict(PATH_INFO=path, REQUEST_METHOD=method, CONTENT_TYPE='application/json',CONTENT_LENGTH=str(len(raw)),wsgi_input=None)
        env['wsgi.input']=io.BytesIO(raw)
        if auth is not None:env['HTTP_AUTHORIZATION']='tma '+auth
        if origin:env['HTTP_ORIGIN']=origin
        def start(status,headers):result.update(status=status,headers=dict(headers))
        content=b''.join(self.app(env,start));result['body']=content
        if result['headers']['Content-Type'].startswith('application/json'):result['json']=json.loads(content)
        return result

    def test_telegram_signature_and_user_id(self):
        self.assertEqual(validate_init_data(signed(),TOKEN)['id'],STUDENT)
        with self.assertRaises(ValueError):validate_init_data(signed().replace('2002','2003'),TOKEN)
        with self.assertRaises(ValueError):validate_init_data(signed(date=int(time.time())-86401),TOKEN)
        with self.assertRaises(ValueError):validate_init_data(signed(date=int(time.time())+100),TOKEN)
        with self.assertRaises(ValueError):validate_init_data(signed()+'&auth_date=1',TOKEN)
        with self.assertRaises(ValueError):validate_init_data(signed(user_id=True),TOKEN)

    def test_anonymous_trial_only(self):
        r=self.request();self.assertEqual(r['status'],'200 OK');j=r['json']
        self.assertEqual(j['access']['book'],3);self.assertEqual(j['access']['hsk'],0)
        self.assertFalse(j['curriculum']['book']);self.assertFalse(j['curriculum']['hsk'])
        self.assertTrue(j['data']['examples']);self.assertIn('你好吗？',j['locale']['examples'])
        for g in j['data']['groups']:
            self.assertTrue(all(m['s'] in {'L1','L2','L3'} for m in g['m']))
        self.assertNotIn('经理',json.dumps(j,ensure_ascii=False))

    def test_shell_never_contains_paid_datasets(self):
        r=self.request('/',method='GET');text=r['body'].decode()
        self.assertIn('window.__HANZI_SERVER__=true',text)
        self.assertIn('const DATA=BOOT.data;',text)
        self.assertNotIn('马马虎虎',text);self.assertNotIn('guò',text)
        self.assertIn("script-src 'nonce-",r['headers']['Content-Security-Policy'])

    def test_source_files_are_not_downloadable(self):
        for path in ['/base.html','/README.md','/server.py','/access_store.py','/.env','/.git/config','/var/access.sqlite3','/locale.json','/curriculum.json']:
            self.assertEqual(self.request(path,method='GET')['status'],'404 Not Found',path)

    def test_forged_auth_never_returns_paid_data(self):
        self.assertEqual(self.request(auth=signed()+'x')['status'],'401 Unauthorized')
        self.assertEqual(self.request(auth='user_id=1001')['status'],'401 Unauthorized')
        self.assertEqual(self.request(body={'user_id':ADMIN,'package':'all'})['json']['access']['book'],3)

    def test_admin_identity_requires_signature(self):
        self.assertEqual(self.request(auth=signed(ADMIN))['json']['access']['hsk'],2)
        self.assertEqual(self.request(auth=signed(ADMIN))['json']['access']['book'],16)

    def test_each_package_and_revocation(self):
        for pkg,book,hsk in [('book8',8,0),('book16',16,0),('hsk1',3,1),('hsk2',3,2),('all',16,2)]:
            self.store.mutate(ADMIN,STUDENT,'grant',pkg)
            j=self.request(auth=signed())['json'];self.assertEqual((j['access']['book'],j['access']['hsk']),(book,hsk))
            self.assertEqual(len(j['curriculum']['hsk']),{0:0,1:300,2:500}[hsk])
            self.store.mutate(ADMIN,STUDENT,'revoke',pkg)
            self.assertEqual(self.request(auth=signed())['json']['access']['book'],3)

    def test_expiry_block_and_unblock(self):
        self.store.mutate(ADMIN,STUDENT,'grant','all',int(time.time())-1)
        self.assertEqual(self.store.permissions(STUDENT)['hsk'],0)
        self.store.mutate(ADMIN,STUDENT,'grant','all')
        before=self.store.permissions(STUDENT)['revision']
        self.store.mutate(ADMIN,STUDENT,'block')
        j=self.request(auth=signed())['json'];self.assertEqual(j['access']['book'],0);self.assertTrue(j['access']['blocked']);self.assertFalse(j['data']['groups']);self.assertFalse(j['curriculum']['hsk'])
        self.assertNotEqual(before,j['access']['revision'])
        self.assertEqual(self.request('/api/request',auth=signed(),body={'package':'hsk1'})['status'],'403 Forbidden')
        self.store.mutate(ADMIN,STUDENT,'unblock');self.assertEqual(self.store.permissions(STUDENT)['hsk'],2)

    def test_pending_requests_are_idempotent_and_persist(self):
        self.assertEqual(self.request('/api/request',body={'package':'all'})['status'],'401 Unauthorized')
        for duplicate in [False,True]:
            j=self.request('/api/request',auth=signed(),body={'package':'hsk1'})['json'];self.assertEqual(j['duplicate'],duplicate)
        reopened=AccessStore(self.store.path);self.assertEqual(len(reopened.pending()),1)
        self.store.mutate(ADMIN,STUDENT,'grant','hsk1');self.assertEqual(reopened.pending(),[])

    def test_update_redelivery_does_not_extend_access(self):
        self.store.mutate(ADMIN,STUDENT,'grant','all',9999999999,123)
        self.assertFalse(self.store.mutate(ADMIN,STUDENT,'grant','all',9999999998,123))
        self.assertEqual(self.store.permissions(STUDENT)['grants'][0]['expires'],9999999999)

    def test_api_method_and_origin(self):
        self.assertEqual(self.request(method='GET')['status'],'405 Method Not Allowed')
        self.assertEqual(self.request(origin='https://evil.test',auth=signed())['status'],'403 Forbidden')
        self.assertEqual(self.request(origin='https://example.test',auth=signed())['status'],'200 OK')

    def bot_callback(self, actor, data, chat=None, kind='private', update_id=100):
        self.app.bot.handle(dict(update_id=update_id,callback_query=dict(id='query',data=data,**{'from':dict(id=actor,first_name='Test')},message=dict(chat=dict(id=actor if chat is None else chat,type=kind)))))

    def test_non_owner_and_group_cannot_grant(self):
        self.bot_callback(STUDENT,f'grant:{STUDENT}:all:forever')
        self.bot_callback(ADMIN,f'grant:{STUDENT}:all:forever',kind='group')
        self.bot_callback(ADMIN,f'grant:{STUDENT}:all:forever',chat=STUDENT)
        self.assertEqual(self.store.permissions(STUDENT)['hsk'],0)

    def test_owner_buttons_grant_revoke_and_block(self):
        self.bot_callback(ADMIN,f'grant:{STUDENT}:hsk2:30',update_id=101)
        self.assertEqual(self.store.permissions(STUDENT)['hsk'],2)
        self.bot_callback(ADMIN,f'revoke:{STUDENT}:hsk2',update_id=102)
        self.assertEqual(self.store.permissions(STUDENT)['hsk'],0)
        self.bot_callback(ADMIN,f'block:{STUDENT}',update_id=103)
        self.assertTrue(self.store.permissions(STUDENT)['blocked'])
        self.bot_callback(ADMIN,f'unblock:{STUDENT}',update_id=104)
        self.assertFalse(self.store.permissions(STUDENT)['blocked'])

    def test_granted_db_survives_restart(self):
        self.store.mutate(ADMIN,STUDENT,'grant','book8');self.store.set_offset(999)
        store=AccessStore(self.store.path);self.assertEqual(store.permissions(STUDENT)['book'],8);self.assertEqual(store.offset(),999)

    def test_english_coverage_for_all_available_material(self):
        j=self.repo_full()
        meanings=[m for g in j['data']['groups'] for m in g['m']]+j['curriculum']['book']+j['curriculum']['hsk']
        self.assertTrue(all(m['r'] in j['locale']['meanings'] for m in meanings))
        self.assertTrue(all(e['h'] in j['locale']['examples'] for e in j['data']['examples']+j['curriculum']['examples']))
        self.assertEqual(j['locale']['meanings']['давать сдачу'],'give change')
        self.assertEqual(j['locale']['meanings']['счётное слово для плоских предметов'],'measure word for flat objects')

    def repo_full(self):return self.app.repo.filtered({'book':16,'hsk':2,'blocked':False})


if __name__=='__main__':unittest.main()
