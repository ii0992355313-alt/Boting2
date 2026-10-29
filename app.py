import os
from pathlib import Path
import secrets
import hmac
import re
import time
import urllib.request
import urllib.error
import json
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, request, abort, jsonify
from sqlalchemy import create_engine, MetaData, Table, Column, String, BigInteger, Integer, Text, select, insert, update, delete, func, case, text as sql_text
from sqlalchemy.pool import NullPool
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from threading import Lock

load_dotenv(Path(__file__).with_name('.env'))
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
OWNER = int(os.getenv('OWNER_ID', '5224030424'))
PRICE = int(os.getenv('PRO_STARS_PRICE', '100'))
TOKEN = os.getenv('BOT_TOKEN', '')
PAYLOAD = 'krolikguard:pro:30d:v1'
meta = MetaData()
subs = Table('subscriptions', meta, Column('user_id', BigInteger, primary_key=True), Column('expires', BigInteger, nullable=False))
logs = Table('event_logs', meta, Column('id', String(40), primary_key=True), Column('created', BigInteger), Column('level', String(20)), Column('event', String(80)), Column('detail', Text))
payments = Table('payments', meta, Column('charge_id', String(255), primary_key=True), Column('user_id', BigInteger), Column('amount', Integer), Column('created', BigInteger))
members = Table('moderation', meta, Column('chat_id', BigInteger, primary_key=True), Column('user_id', BigInteger, primary_key=True), Column('warnings', Integer, default=0), Column('banned', Integer, default=0), Column('muted_until', BigInteger, default=0))
processed = Table('processed_updates', meta, Column('update_id', BigInteger, primary_key=True))

_engine = None
_engine_lock = Lock()
_schema_ready = False

def engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL') or ''
                if not url:
                    if os.getenv('VERCEL'):
                        raise RuntimeError('DATABASE_URL required; attach PostgreSQL storage')
                    url = 'sqlite:///bot-local.db'
                if url.startswith(('postgres://', 'postgresql://')):
                    url = 'postgresql+psycopg://' + url.split('://', 1)[1]
                if os.getenv('VERCEL') and not url.startswith('postgresql+psycopg://'):
                    raise RuntimeError('Production requires persistent PostgreSQL')
                args = {'poolclass': NullPool}
                if url.startswith('postgresql+psycopg://'):
                    args['connect_args'] = {'connect_timeout': 4, 'prepare_threshold': None}
                _engine = create_engine(url, **args)
    return _engine


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    # Serialize schema creation across concurrent serverless instances.
    with engine().begin() as conn:
        if conn.dialect.name == 'postgresql':
            conn.execute(sql_text('SELECT pg_advisory_xact_lock(612178431)'))
        meta.create_all(conn)
    _schema_ready = True


def dialect_insert(conn, table):
    return (pg_insert if conn.dialect.name == 'postgresql' else sqlite_insert)(table)


def audit(conn, event, detail='', level='info'):
    conn.execute(insert(logs).values(id=secrets.token_hex(16), created=int(time.time()), level=level, event=event, detail=str(detail)[:1500]))

class TelegramError(RuntimeError):
    def __init__(self, method, code):
        self.method = method
        self.code = code
        super().__init__(f'Telegram {method}: {code}')


def telegram(method, **data):
    if not TOKEN:
        raise RuntimeError('BOT_TOKEN required')
    req = urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/{method}', data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=6) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Only code and method are retained: the request URL contains a credential.
        try:
            payload = json.load(exc)
        except Exception:
            payload = {}
        desc = payload.get('description','').lower()
        if method == 'deleteMessage' and 'message to delete not found' in desc:
            return True
        raise TelegramError(method, payload.get('error_code', exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Telegram API unavailable') from None
    if not result.get('ok'):
        raise TelegramError(method, result.get('error_code',500))
    return result.get('result')

def send(chat, text, **kwargs):
    return telegram('sendMessage', chat_id=chat, text=text, **kwargs)

def is_pro(conn, uid):
    exp = conn.execute(select(subs.c.expires).where(subs.c.user_id == uid)).scalar_one_or_none()
    return uid == OWNER or (exp is not None and (exp == 0 or exp > time.time()))

def grant(conn, uid):
    # Atomic extension; duplicate charge IDs are rejected before this operation.
    now = int(time.time())
    stmt = dialect_insert(conn, subs).values(user_id=uid, expires=now+30*86400)
    conn.execute(stmt.on_conflict_do_update(index_elements=['user_id'], set_={'expires':case((subs.c.expires == 0, 0), (subs.c.expires > now, subs.c.expires+30*86400), else_=now+30*86400)}))

def upsert_member(conn, chat, uid, **values):
    stmt = dialect_insert(conn, members).values(chat_id=chat, user_id=uid, **values)
    conn.execute(stmt.on_conflict_do_update(index_elements=['chat_id','user_id'], set_=values))

def valid_payment(p):
    return p.get('currency') == 'XTR' and p.get('total_amount') == PRICE and p.get('invoice_payload') in (PAYLOAD, 'krolik_pro_subscription_payload')

def receive_payment(conn, uid, p):
    if not valid_payment(p) or not p.get('telegram_payment_charge_id'):
        audit(conn, 'payment_rejected', str(uid), 'error')
        return False
    stmt = dialect_insert(conn, payments).values(charge_id=p['telegram_payment_charge_id'], user_id=uid, amount=PRICE, created=int(time.time())).on_conflict_do_nothing(index_elements=['charge_id'])
    if conn.execute(stmt).rowcount != 1:
        return False
    grant(conn, uid)
    audit(conn, 'payment_success', f'{uid}: {PRICE} XTR')
    return True

def invoice(chat):
    return telegram('sendInvoice', chat_id=chat, title='KrolikGuard PRO · 30 дней', description='Доступ к PRO-командам модерации. Права администратора чата обязательны.', payload=PAYLOAD, provider_token='', currency='XTR', prices=[{'label':'PRO на 30 дней','amount':PRICE}])

def is_admin(chat, uid):
    if uid == OWNER:
        return True
    return telegram('getChatMember', chat_id=chat, user_id=uid).get('status') in ('creator','administrator')

BAD_WORDS = ['хуй', 'пизд', 'ебат', 'бляд', 'сук', 'еблан', 'пидор', 'блять', 'хуесос', 'залуп', 'мудак', 'гондон', 'шлюх', 'чмо', 'пидри', 'уеб', 'разъеб', 'отхуя', 'доеб', 'заеб', 'нахуй', 'похуй', 'сука', 'мразь', 'урод', 'сволочь', 'дебил', 'идиот']
ALLOWED_DOMAINS = {'t.me','youtube.com','github.com'}
def unsafe_link(text):
    for raw in re.findall(r'(?:https?://|www\.)[^\s<>]+|(?:t\.me|vk\.com|telegram\.me)/[^\s<>]+', text, re.I):
        host = (urlparse(raw if '://' in raw else 'https://'+raw).hostname or '').lower()
        if not any(host == d or host.endswith('.'+d) for d in ALLOWED_DOMAINS):
            return True
    return bool(re.search(r'(?<!\w)@[a-zA-Z0-9_]{5,}', text))

def process_message(conn, msg):
    chat = msg['chat']['id']; uid = msg.get('from', {}).get('id')
    if not uid:
        return
    if msg.get('successful_payment'):
        # Commit delivery before notification, so Telegram send failures cannot undo a payment.
        return receive_payment(conn, uid, msg['successful_payment'])
    text = msg.get('text') or msg.get('caption', '')
    parts = text.split()
    command = parts[0].split('@')[0] if parts else ''
    if parts and '@' in parts[0]:
        own_name = os.getenv('BOT_USERNAME','').lstrip('@').lower()
        if own_name and parts[0].split('@',1)[1].lower() != own_name: return
    group = msg['chat'].get('type') in ('group','supergroup')
    if command in ('/start','/buy'):
        if command == '/buy' or text.split()[-1:] == ['buy']:
            invoice(uid)
        else:
            send(chat, '🐰 KrolikGuard\nЗащита чатов от ссылок и мата. PRO — 30 дней.\n/help — команды · /terms — условия · /paysupport — поддержка', reply_markup={'inline_keyboard':[[{'text':f'⭐ PRO за {PRICE} Stars','callback_data':'buy_stars_pro'}],[{'text':'Мой статус','callback_data':'check_status'}]]})
        return
    if command in ('/help','/cmds','/terms','/paysupport'):
        send(chat, f'PRO: {PRICE} Stars / 30 дней. Повторная покупка продлевает срок.\n/ban, /unban, /mute 30m, /unmute — ответом на сообщение; для PRO-администраторов.\n/status — подписка. /grant ID и /revoke ID — владелец.\nПоддержка и возвраты: обратитесь к владельцу (Telegram ID {OWNER}). Для обращения укажите номер платежа из квитанции. Возврат рассматривается владельцем через Telegram Stars API.')
        return
    if command == '/status':
        send(chat, 'PRO активен ✅' if is_pro(conn, uid) else 'PRO не активен. /buy — купить')
        return
    if command in ('/logs', '/stats'):
        if uid != OWNER or group:
            send(chat, 'Команда доступна владельцу в личных сообщениях боту.'); return
        if command == '/logs':
            rows = conn.execute(select(logs).order_by(logs.c.created.desc()).limit(15)).mappings().all()
            report = '\n'.join(f"{r['event']}: {r['detail']}" for r in rows) or 'Событий пока нет.'
        else:
            count = conn.execute(select(func.count()).select_from(subs).where((subs.c.expires == 0) | (subs.c.expires > int(time.time())))).scalar()
            revenue = conn.execute(select(func.coalesce(func.sum(payments.c.amount),0))).scalar()
            report = f'Активных PRO: {count}\nПолучено: {revenue} Stars'
        send(chat, report[:3900]); return
    if command in ('/grant','/revoke'):
        if uid != OWNER:
            send(chat, 'Недостаточно прав.'); return
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            send(chat, 'Формат: /grant ID или /revoke ID'); return
        target = int(parts[1])
        if command == '/grant': grant(conn, target)
        else: conn.execute(delete(subs).where(subs.c.user_id == target))
        audit(conn, command[1:], str(target)); send(chat, 'Подписка обновлена.'); return
    if command in ('/ban','/unban','/mute','/unmute'):
        if not group or not is_pro(conn, uid) or not is_admin(chat, uid):
            send(chat, 'Нужны PRO и права администратора группы.'); return
        target = msg.get('reply_to_message', {}).get('from', {}).get('id')
        if not target:
            send(chat, 'Отправьте команду ответом на сообщение.'); return
        if is_admin(chat, target):
            send(chat, 'Нельзя ограничить администратора.'); return
        if command in ('/ban','/unban'):
            telegram('banChatMember' if command == '/ban' else 'unbanChatMember', chat_id=chat, user_id=target, **({'only_if_banned':True} if command == '/unban' else {}))
            upsert_member(conn, chat, target, banned=int(command == '/ban'))
        else:
            duration = 1800
            if command == '/mute' and len(text.split()) > 1:
                m = re.fullmatch(r'(\d+)([mhd])', text.split()[1])
                if not m:
                    send(chat, 'Время: 30m, 2h или 1d'); return
                duration = int(m[1])*{'m':60,'h':3600,'d':86400}[m[2]]
                if not 60 <= duration <= 366*86400:
                    send(chat, 'Допустимо от 1 минуты до 366 дней.'); return
            muted = command == '/mute'
            until = int(time.time())+duration if muted else 0
            permissions = {k:not muted for k in ('can_send_messages','can_send_audios','can_send_documents','can_send_photos','can_send_videos','can_send_video_notes','can_send_voice_notes','can_send_polls','can_send_other_messages','can_add_web_page_previews')}
            if not muted:
                permissions = telegram('getChat', chat_id=chat).get('permissions', permissions)
            telegram('restrictChatMember', chat_id=chat, user_id=target, permissions=permissions, until_date=until, use_independent_chat_permissions=True)
            upsert_member(conn, chat, target, muted_until=until)
        audit(conn, command[1:], f'chat={chat}, user={target}, actor={uid}')
        send(chat, 'Готово.'); return
    if group and text and not is_admin(chat, uid):
        state = conn.execute(select(members).where(members.c.chat_id == chat, members.c.user_id == uid)).mappings().first()
        reason = 'ссылка' if unsafe_link(text) else ('мат' if any(w in text.lower() for w in BAD_WORDS) else '')
        if reason or (state and (state['banned'] or state['muted_until'] > time.time())):
            telegram('deleteMessage', chat_id=chat, message_id=msg['message_id'])
            if reason:
                count = (state['warnings'] if state else 0)+1
                if count >= 3:
                    telegram('banChatMember', chat_id=chat, user_id=uid)
                upsert_member(conn, chat, uid, warnings=count, banned=int(count >= 3))
                audit(conn, 'auto_moderation', f'chat={chat}, user={uid}, reason={reason}, warnings={count}')
                send(chat, f'Нарушение: {reason}. Предупреждения: {count}/3.' if count < 3 else 'Участник заблокирован за 3 нарушения.')

@app.post('/webhook')
def webhook():
    secret = os.getenv('WEBHOOK_SECRET', '')
    if not secret or not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token',''), secret):
        abort(403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('update_id'), int): abort(400)
    try:
        paid_granted = False
        ensure_schema()
        if 'pre_checkout_query' in data:
            q = data['pre_checkout_query']
            with engine().connect() as conn:
                conn.execute(select(subs.c.user_id).limit(1))
            valid = valid_payment(q)
            telegram('answerPreCheckoutQuery', pre_checkout_query_id=q['id'], ok=valid, **({} if valid else {'error_message':'Неверный счёт. Создайте новый через /buy.'}))
        else:
            with engine().begin() as conn:
                claimed = conn.execute(dialect_insert(conn, processed).values(update_id=data['update_id']).on_conflict_do_nothing(index_elements=['update_id'])).rowcount
                if claimed != 1: return jsonify(ok=True)
                if 'message' in data: paid_granted = process_message(conn, data['message'])
                if 'callback_query' in data:
                    cb = data['callback_query']; uid = cb['from']['id']
                    telegram('answerCallbackQuery', callback_query_id=cb['id'])
                    if cb.get('data') == 'buy_stars_pro': invoice(uid)
                    elif cb.get('data') == 'check_status': send(uid, 'PRO активен ✅' if is_pro(conn, uid) else 'PRO не активен. /buy')
        paid = data.get('message', {}).get('successful_payment')
        if paid and paid_granted:
            try: send(data['message']['chat']['id'], 'Оплата получена. PRO активирован или продлён на 30 дней. /status — проверить.')
            except RuntimeError: pass
        return jsonify(ok=True)
    except TelegramError as exc:
        app.logger.error('Telegram method failed: %s %s', exc.method, exc.code)
        try:
            with engine().begin() as conn: audit(conn, 'telegram_error', f'{exc.method}: {exc.code}', 'error')
        except Exception: pass
        # Permanent API errors (expired callbacks, insufficient rights, blocked bot)
        # must not pin the Telegram queue. Network errors and rate limits are retried.
        if exc.code in (400, 401, 403, 404): return jsonify(ok=True)
        return jsonify(ok=False),503
    except Exception as exc:
        # Do not log request bodies, tokens, or raw network exception URLs.
        app.logger.error('Webhook processing failed: %s', type(exc).__name__)
        try:
            with engine().begin() as conn: audit(conn, 'webhook_error', type(exc).__name__, 'error')
        except Exception: pass
        return jsonify(ok=False), 503

@app.get('/')
def root():
    return jsonify(service='KrolikGuard Telegram Bot', mode='webhook', health='/health')


def authorized_setup():
    secret = os.getenv('WEBHOOK_SECRET','')
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret)) and hmac.compare_digest(request.headers.get('Authorization',''), 'Bearer '+secret)


def register_webhook(site):
    ensure_schema()
    me = telegram('getMe')
    telegram('setWebhook', url=site+'/webhook', secret_token=os.environ['WEBHOOK_SECRET'], allowed_updates=['message','callback_query','pre_checkout_query'], max_connections=1, drop_pending_updates=False)
    telegram('setMyCommands', commands=[{'command':'start','description':'Открыть меню'},{'command':'buy','description':'Купить PRO за Stars'},{'command':'status','description':'Моя подписка'},{'command':'help','description':'Команды и правила'},{'command':'paysupport','description':'Поддержка платежей'}])
    info = telegram('getWebhookInfo')
    with engine().begin() as conn: audit(conn, 'webhook_connected', site)
    return dict(ok=info.get('url') == site+'/webhook', bot=me.get('username'), webhook=info.get('url'), pending=info.get('pending_update_count',0))


@app.post('/connect')
def connect():
    if not authorized_setup(): abort(403)
    site = os.getenv('SITE_URL','').rstrip('/')
    parsed = urlparse(site)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return jsonify(ok=False, error='Set SITE_URL to your production HTTPS domain, without a path'),400
    try:
        return jsonify(register_webhook(site))
    except Exception as exc:
        app.logger.error('Connection failed: %s',type(exc).__name__)
        return jsonify(ok=False,error='Cannot connect. Check BOT_TOKEN, PostgreSQL and Vercel runtime logs.'),503


@app.get('/diagnostics')
def diagnostics():
    if not authorized_setup(): abort(403)
    try:
        ensure_schema()
        info = telegram('getWebhookInfo')
        return jsonify(ok=True, webhook=info.get('url'), pending=info.get('pending_update_count',0), last_error=info.get('last_error_message'), last_error_date=info.get('last_error_date'))
    except Exception:
        return jsonify(ok=False),503


@app.get('/health')
def health():
    try:
        ensure_schema()
        with engine().connect() as conn: conn.execute(select(subs.c.user_id).limit(1))
        return jsonify(ok=True, database='ready')
    except Exception:
        return jsonify(ok=False, error='Database unavailable; check DATABASE_URL'),503


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.errorhandler(500)
def server_error(error):
    return jsonify(ok=False,error='Internal error. See Vercel runtime logs.'),500


if __name__ == '__main__':
    app.run(host='127.0.0.1',port=5000)
