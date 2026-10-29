import os
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ['WEBHOOK_SECRET']='test-secret-123456789012345678901234'
os.environ['SITE_URL']='https://example.vercel.app'
import app as target
from sqlalchemy import create_engine, select, func

class BotServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        target._engine=create_engine('sqlite:///'+self.temp.name+'/test.db')
        target._schema_ready=False
        target.ensure_schema()
        target.app.config['TESTING']=True
        self.client=target.app.test_client()
    def tearDown(self):
        target._engine.dispose(); target._engine=None; target._schema_ready=False; self.temp.cleanup()
    def payment(self, charge='charge1', amount=None):
        return {'currency':'XTR','total_amount':target.PRICE if amount is None else amount,'invoice_payload':target.PAYLOAD,'telegram_payment_charge_id':charge}
    def post(self,data,secret=True):
        return self.client.post('/webhook',json=data,headers={'X-Telegram-Bot-Api-Secret-Token':os.environ['WEBHOOK_SECRET']} if secret else {})
    def message(self,text='/start',uid=123,chat=123):
        return {'chat':{'id':chat,'type':'private' if chat>0 else 'supergroup'},'from':{'id':uid},'text':text,'message_id':10}
    def test_root_has_no_website(self):
        response=self.client.get('/'); self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['mode'],'webhook')
        self.assertEqual(self.client.get('/admin').status_code,404)
    def test_schema_and_health(self):
        target.meta.drop_all(target.engine()); target._schema_ready=False
        self.assertTrue(self.client.get('/health').json['ok'])
    def test_secret_required(self): self.assertEqual(self.post({'update_id':1},False).status_code,403)
    def test_invalid_body(self): self.assertEqual(self.post({'message':{}}).status_code,400)
    def test_start_and_duplicate(self):
        with patch.object(target,'telegram',return_value=True) as call:
            self.assertEqual(self.post({'update_id':1,'message':self.message()}).status_code,200)
            self.post({'update_id':1,'message':self.message()})
            self.assertEqual(call.call_count,1)
    def test_pre_checkout(self):
        for amount,accepted in [(target.PRICE,True),(1,False)]:
            q=self.payment(amount=amount); q['id']='q'
            with patch.object(target,'telegram',return_value=True) as call:
                self.assertEqual(self.post({'update_id':10,'pre_checkout_query':q}).status_code,200)
                self.assertEqual(call.call_args.kwargs['ok'],accepted)
    def test_payment_idempotence_extension(self):
        with target.engine().begin() as c:
            self.assertTrue(target.receive_payment(c,123,self.payment()))
            first=c.execute(select(target.subs.c.expires)).scalar_one()
            self.assertFalse(target.receive_payment(c,123,self.payment()))
            target.receive_payment(c,123,self.payment('charge2'))
            self.assertEqual(c.execute(select(target.subs.c.expires)).scalar_one(),first+30*86400)
    def test_payment_wrong_amount_rejected(self):
        with target.engine().begin() as c:
            self.assertFalse(target.receive_payment(c,123,self.payment(amount=1)))
            self.assertEqual(c.execute(select(func.count()).select_from(target.subs)).scalar(),0)
    def test_payment_survives_send_failure(self):
        msg=self.message(); msg['successful_payment']=self.payment()
        with patch.object(target,'telegram',side_effect=RuntimeError('network')):
            self.assertEqual(self.post({'update_id':12,'message':msg}).status_code,200)
        with target.engine().connect() as c: self.assertTrue(target.is_pro(c,123))
    def test_rejected_payment_no_success_message(self):
        msg=self.message(); msg['successful_payment']=self.payment(amount=1)
        with patch.object(target,'telegram') as call:
            self.post({'update_id':12,'message':msg}); call.assert_not_called()
    def test_network_failure_retries(self):
        with patch.object(target,'telegram',side_effect=RuntimeError('network')):
            self.assertEqual(self.post({'update_id':3,'message':self.message()}).status_code,503)
        with target.engine().connect() as c: self.assertEqual(c.execute(select(func.count()).select_from(target.processed)).scalar(),0)
    def test_permanent_error_not_blocking_queue(self):
        with patch.object(target,'telegram',side_effect=target.TelegramError('sendMessage',403)):
            self.assertEqual(self.post({'update_id':5,'message':self.message()}).status_code,200)
    def test_pro_without_admin_cannot_ban(self):
        with target.engine().begin() as c: target.grant(c,123)
        msg=self.message('/ban',chat=-1); msg['reply_to_message']={'from':{'id':456}}
        with patch.object(target,'telegram',return_value={'status':'member'}) as call:
            self.post({'update_id':15,'message':msg})
            self.assertNotIn('banChatMember',[c.args[0] for c in call.call_args_list])
    def test_grant_owner_only(self):
        with patch.object(target,'telegram',return_value=True): self.post({'update_id':16,'message':self.message('/grant 456')})
        with target.engine().connect() as c: self.assertFalse(target.is_pro(c,456))
    def test_owner_grant(self):
        with patch.object(target,'telegram',return_value=True): self.post({'update_id':17,'message':self.message('/grant 456',uid=target.OWNER)})
        with target.engine().connect() as c: self.assertTrue(target.is_pro(c,456))
    def test_stats_owner_only(self):
        with patch.object(target,'telegram',return_value=True) as call:
            self.post({'update_id':18,'message':self.message('/stats')})
            self.assertIn('владельцу',call.call_args.kwargs['text'])
    def test_link_domains(self):
        self.assertFalse(target.unsafe_link('https://github.com/a'))
        self.assertTrue(target.unsafe_link('https://github.com.evil.test'))
        self.assertTrue(target.unsafe_link('https://t.me/good https://evil.test'))
    def test_whitespace_message(self):
        with patch.object(target,'telegram') as call:
            self.assertEqual(self.post({'update_id':19,'message':self.message('   ')}).status_code,200)
            call.assert_not_called()
    def test_setup_protected(self): self.assertEqual(self.client.post('/connect').status_code,403)
    def test_connect_registers_secret_and_checks_url(self):
        def api(method,**kw):
            if method=='getMe': return {'username':'test_bot'}
            if method=='getWebhookInfo': return {'url':'https://example.vercel.app/webhook','pending_update_count':0}
            return True
        with patch.object(target,'telegram',side_effect=api) as call:
            response=self.client.post('/connect',headers={'Authorization':'Bearer '+os.environ['WEBHOOK_SECRET']})
            self.assertEqual(response.status_code,200); self.assertTrue(response.json['ok'])
            webhook=[c for c in call.call_args_list if c.args[0]=='setWebhook'][0]
            self.assertEqual(webhook.kwargs['secret_token'],os.environ['WEBHOOK_SECRET'])
            self.assertFalse(webhook.kwargs['drop_pending_updates'])
    def test_database_persists_across_restart(self):
        with target.engine().begin() as c: target.receive_payment(c,123,self.payment())
        url=str(target._engine.url); target._engine.dispose(); target._engine=create_engine(url); target._schema_ready=False
        target.ensure_schema()
        with target.engine().connect() as c: self.assertTrue(target.is_pro(c,123))

if __name__=='__main__': unittest.main()
