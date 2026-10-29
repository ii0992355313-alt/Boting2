"""Runs on Vercel during Production builds; no local bot process is needed."""
import os
import re


def main():
    if os.getenv('VERCEL_ENV') != 'production':
        print('Webhook setup skipped: only Production deployments register the bot.')
        return
    # Validate before importing the service. Never print credential values.
    missing = [key for key in ('BOT_TOKEN','WEBHOOK_SECRET','SITE_URL') if not os.getenv(key)]
    if not (os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL')):
        missing.append('DATABASE_URL or POSTGRES_URL')
    if missing:
        raise SystemExit('Set Vercel Production environment variables: ' + ', '.join(missing))
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', os.environ['WEBHOOK_SECRET']):
        raise SystemExit('WEBHOOK_SECRET must contain 32-256 letters, digits, underscores or hyphens.')
    from urllib.parse import urlparse
    site = os.environ['SITE_URL'].rstrip('/')
    url = urlparse(site)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise SystemExit('SITE_URL must be the production HTTPS domain without a path.')
    try:
        from app import register_webhook
        result = register_webhook(site)
        if not result.get('ok'):
            raise RuntimeError('Webhook address mismatch')
    except Exception as exc:
        raise SystemExit('Telegram setup failed (' + type(exc).__name__ + '). Check BOT_TOKEN, database availability and Production variables. No tokens are printed.') from None
    print('Telegram webhook connected. After deployment is Ready, send /start to the bot.')


if __name__ == '__main__':
    main()
