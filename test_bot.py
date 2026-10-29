import os
import tempfile
import unittest
from unittest.mock import patch
os.environ['BOT_TOKEN']='test-token'
import bot

class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        bot.db=bot.KrolikDatabase(self.temp.name+'/test.json')
        self.core=bot.KrolikGuardCore()
    def tearDown(self): self.temp.cleanup()
    def message(self, charge='charge1', amount=100):
        return {'chat':{'id':123,'type':'private'},'from':{'id':123},'successful_payment':{'currency':'XTR','total_amount':amount,'invoice_payload':'krolik_pro_subscription_payload','telegram_payment_charge_id':charge}}
    def test_success_duplicate_and_extension(self):
        with patch.object(bot.TelegramProtocol,'send_text'):
            self.core.process_message(self.message())
            first=bot.db.subscribers['123']
            self.core.process_message(self.message())
            self.assertEqual(first,bot.db.subscribers['123'])
            self.core.process_message(self.message('charge2'))
            self.assertAlmostEqual(bot.db.subscribers['123']-first,30*86400,delta=1)
    def test_reject_invalid_payment(self):
        with patch.object(bot.TelegramProtocol,'send_text'):
            self.core.process_message(self.message(amount=1))
        self.assertFalse(bot.db.is_pro(123))
    def test_persistent_charge(self):
        with patch.object(bot.TelegramProtocol,'send_text'): self.core.process_message(self.message())
        reloaded=bot.KrolikDatabase(bot.db.filepath)
        self.assertIn('charge1',reloaded.charges)
        self.assertTrue(reloaded.is_pro(123))
    def test_pre_checkout_approval(self):
        query={'id':'q1','currency':'XTR','total_amount':100,'invoice_payload':'krolik_pro_subscription_payload'}
        with patch.object(bot.TelegramProtocol,'fetch_updates',side_effect=[[{'update_id':1,'pre_checkout_query':query}],KeyboardInterrupt()]), patch.object(bot.TelegramProtocol,'call_api',return_value=True) as api:
            with self.assertRaises(KeyboardInterrupt): self.core.start_polling()
            self.assertEqual(api.call_args.args[0],'answerPreCheckoutQuery')
            self.assertTrue(api.call_args.args[1]['ok'])
    def test_moderation_requires_admin(self):
        bot.db.add_pro(123)
        msg={'chat':{'id':-100,'type':'supergroup'},'from':{'id':123},'text':'/ban','reply_to_message':{'from':{'id':456}}}
        with patch.object(bot,'telegram_admin',return_value=False),patch.object(bot.TelegramProtocol,'send_text'),patch.object(bot.TelegramProtocol,'ban_member') as ban:
            self.core.process_message(msg)
            ban.assert_not_called()

if __name__=='__main__': unittest.main()
