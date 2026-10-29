import unittest
from unittest.mock import patch
import setup_server

class SetupTests(unittest.TestCase):
    def test_preview_does_not_change_webhook(self):
        with patch.dict('os.environ',{'VERCEL_ENV':'preview'},clear=True), patch('app.register_webhook') as register:
            setup_server.main(); register.assert_not_called()
    def test_production_requires_variables(self):
        with patch.dict('os.environ',{'VERCEL_ENV':'production'},clear=True):
            with self.assertRaisesRegex(SystemExit,'BOT_TOKEN'): setup_server.main()
    def test_production_registers(self):
        env={'VERCEL_ENV':'production','BOT_TOKEN':'test-token','WEBHOOK_SECRET':'a'*40,'DATABASE_URL':'postgresql://example.invalid/db','SITE_URL':'https://example.vercel.app'}
        with patch.dict('os.environ',env,clear=True), patch('app.register_webhook',return_value={'ok':True}) as register:
            setup_server.main(); register.assert_called_once_with('https://example.vercel.app')
    def test_setup_failure_fails_build_without_secret(self):
        env={'VERCEL_ENV':'production','BOT_TOKEN':'test-token','WEBHOOK_SECRET':'a'*40,'DATABASE_URL':'postgresql://example.invalid/db','SITE_URL':'https://example.vercel.app'}
        with patch.dict('os.environ',env,clear=True), patch('app.register_webhook',side_effect=RuntimeError('private-data')):
            with self.assertRaises(SystemExit) as error: setup_server.main()
            self.assertNotIn('private-data',str(error.exception))
