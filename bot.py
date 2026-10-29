import getpass
from pathlib import Path
import os
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip() or getpass.getpass('Токен бота из BotFather (ввод скрыт): ').strip()
if not BOT_TOKEN:
    raise SystemExit('Токен не указан.')
API_URL = 'https://api.telegram.org/bot' + BOT_TOKEN
DB_FILE = str(Path(__file__).resolve().parent / 'bot_database.json')
import urllib.request
import urllib.parse
import json
import time
import re
import os
import threading
OWNER_ID = 5224030424
RABBIT_GIF_URL = 'https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif'
PRO_STARS_PRICE = 100
CORE_PROFANITY_VOCABULARY = ['хуй', 'пизд', 'ебат', 'бляд', 'сук', 'еблан', 'пидор', 'блять', 'хуесос', 'залуп', 'мудак', 'гондон', 'шлюх', 'чмо', 'пидри', 'уеб', 'разъеб', 'отхуя', 'доеб', 'заеб', 'нахуй', 'похуй', 'сука', 'мразь', 'урод', 'сволочь', 'дебил', 'идиот']
WHITELISTED_DOMAINS = ['t.me', 'youtube.com', 'github.com']

class KrolikDatabase:

    def __init__(self, filepath=DB_FILE):
        print('💾 [DATABASE]: Загрузка базы данных...')
        self.filepath = filepath
        self.subscribers = {}
        self.blacklists = set()
        self.mutes = {}
        self.warnings = {}
        self.boot_time = time.time()
        self.charges = set()
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.charges = set(data.get('charges', []))
                    self.subscribers = data.get('subscribers', {})
                    self.blacklists = set(data.get('blacklists', []))
                    self.mutes = data.get('mutes', {})
                    self.warnings = data.get('warnings', {})
                    print(f'✅ [DB LOADED]: PRO-клиентов: {len(self.subscribers)} | ЧС: {len(self.blacklists)}')
            except Exception as e:
                print(f'❌ [DB ERROR]: {e}')
        else:
            print('ℹ️ [DB]: Создана новая база данных.')

    def save(self):
        data = {'charges': sorted(self.charges), 'subscribers': self.subscribers, 'blacklists': list(self.blacklists), 'mutes': self.mutes, 'warnings': self.warnings}
        try:
            with open(self.filepath + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(self.filepath + '.tmp', self.filepath)
        except Exception as e:
            print(f'❌ [DB SAVE ERROR]: {e}')

    def is_pro(self, uid):
        uid_str = str(uid)
        if uid_str == str(OWNER_ID):
            return True
        if uid_str in self.subscribers:
            expire = self.subscribers[uid_str]
            if expire == 0 or time.time() < expire:
                return True
            else:
                del self.subscribers[uid_str]
                self.save()
        return False

    def add_pro(self, uid, days=30):
        uid_str = str(uid)
        expire = 0 if days == 0 else max(time.time(), self.subscribers.get(uid_str, 0)) + days * 86400
        self.subscribers[uid_str] = expire
        self.save()

    def remove_pro(self, uid):
        uid_str = str(uid)
        if uid_str in self.subscribers:
            del self.subscribers[uid_str]
            self.save()
            return True
        return False

    def is_muted(self, uid):
        uid_str = str(uid)
        if uid_str in self.mutes:
            expire = self.mutes[uid_str]
            if expire == 0 or time.time() < expire:
                return True
            else:
                del self.mutes[uid_str]
                self.save()
        return False

    def get_uptime(self):
        dur = int(time.time() - self.boot_time)
        (h, rem) = divmod(dur, 3600)
        (m, s) = divmod(rem, 60)
        return f'{h}ч {m}м {s}с'
db = KrolikDatabase()

class TelegramProtocol:

    @staticmethod
    def call_api(method, payload):
        url = f'{API_URL}/{method}'
        encoded_data = json.dumps(payload).encode('utf-8')
        try:
            req = urllib.request.Request(url, data=encoded_data, method='POST', headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                return res.get('ok', False)
        except Exception as exc:
            print('Telegram API: ошибка ' + method + ' (' + type(exc).__name__ + ')', flush=True)
            return False

    @classmethod
    def send_text(cls, chat_id, text, markup=None):
        packet = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
        if markup:
            packet['reply_markup'] = json.dumps(markup)
        return cls.call_api('sendMessage', packet)

    @classmethod
    def send_media(cls, chat_id, anim_url, caption='', markup=None):
        packet = {'chat_id': chat_id, 'animation': anim_url, 'caption': caption, 'parse_mode': 'Markdown'}
        if markup:
            packet['reply_markup'] = json.dumps(markup)
        return cls.call_api('sendAnimation', packet)

    @classmethod
    def delete_msg(cls, chat_id, msg_id):
        return cls.call_api('deleteMessage', {'chat_id': chat_id, 'message_id': msg_id})

    @classmethod
    def ban_member(cls, chat_id, uid):
        return cls.call_api('banChatMember', {'chat_id': chat_id, 'user_id': uid})

    @classmethod
    def confirm_callback(cls, q_id, text=''):
        return cls.call_api('answerCallbackQuery', {'callback_query_id': q_id, 'text': text, 'show_alert': False})

    @classmethod
    def send_stars_invoice(cls, chat_id):
        packet = {'chat_id': chat_id, 'title': '🐰 KrolikGuard PRO (30 дней)', 'description': 'Элитная защита чата, полный доступ к командам модерации античита.', 'payload': 'krolik_pro_subscription_payload', 'currency': 'XTR', 'prices': json.dumps([{'label': 'PRO Подписка', 'amount': PRO_STARS_PRICE}])}
        return cls.call_api('sendInvoice', packet)

    @staticmethod
    def fetch_updates(offset=None):
        url = f'{API_URL}/getUpdates?timeout=20'
        if offset:
            url += f'&offset={offset}'
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=25) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                if res.get('ok'):
                    return res.get('result', [])
        except Exception:
            time.sleep(3)
        return []

class SecurityAnalyzer:

    @staticmethod
    def scan_profanity(text):
        if not text:
            return False
        clean = re.sub('[^а-яёa-z\\s]', '', text.lower())
        for token in clean.split():
            for bad in CORE_PROFANITY_VOCABULARY:
                if bad in token:
                    return True
        return False

    @staticmethod
    def scan_links(text):
        if not text:
            return False
        for domain in WHITELISTED_DOMAINS:
            if domain.lower() in text.lower():
                return False
        patterns = ['t\\.me/', 'https?://', 'www\\.', 'vk\\.com/', 'telegram\\.me/', '@[a-zA-Z0-9_]{5,}']
        for p in patterns:
            if re.search(p, text, re.IGNORECASE):
                return True
        return False

class KrolikGuardCore:

    def __init__(self):
        self.offset = 0

    def parse_time(self, time_str):
        match = re.match('^(\\d+)([mhd])$', time_str.lower())
        if not match:
            return None
        (val, unit) = (int(match.group(1)), match.group(2))
        if unit == 'm':
            return val * 60
        elif unit == 'h':
            return val * 3600
        elif unit == 'd':
            return val * 86400
        return None

    def process_message(self, msg):
        chat = msg.get('chat', {})
        chat_id = chat.get('id')
        chat_type = chat.get('type', 'private')
        msg_id = msg.get('message_id')
        user = msg.get('from', {})
        uid = user.get('id')
        username = user.get('username') or user.get('first_name', 'Участник')
        text = msg.get('text', '')
        if not chat_id:
            return
        uid_str = str(uid)
        if msg.get('successful_payment'):
            p = msg['successful_payment']
            if p.get('currency') != 'XTR' or p.get('total_amount') != PRO_STARS_PRICE or p.get('invoice_payload') != 'krolik_pro_subscription_payload':
                print('Отклонён платёж с неверными параметрами', flush=True)
                return
            charge = p.get('telegram_payment_charge_id')
            if not charge or charge in db.charges:
                return
            db.charges.add(charge)
            db.add_pro(uid, days=30)
            TelegramProtocol.send_text(chat_id, '✅ Оплата получена. PRO активирован или продлён на 30 дней!')
            return
        if uid_str in db.blacklists or db.is_muted(uid):
            TelegramProtocol.delete_msg(chat_id, msg_id)
            return
        if not text:
            return
        if chat_type in ['group', 'supergroup'] and uid != OWNER_ID and (not db.is_pro(uid)):
            if SecurityAnalyzer.scan_links(text):
                TelegramProtocol.delete_msg(chat_id, msg_id)
                TelegramProtocol.send_text(chat_id, f'🚫 @{username}, рекламные ссылки запрещены!')
                return
            if SecurityAnalyzer.scan_profanity(text):
                TelegramProtocol.delete_msg(chat_id, msg_id)
                count = db.warnings.get(uid_str, 0) + 1
                db.warnings[uid_str] = count
                db.save()
                if count >= 3:
                    db.blacklists.add(uid_str)
                    db.save()
                    TelegramProtocol.ban_member(chat_id, uid)
                    TelegramProtocol.send_text(chat_id, f'🔨 @{username} заблокирован за 3 предупреждения из 3!')
                else:
                    TelegramProtocol.send_text(chat_id, f'⚠️ @{username}, предупреждение за мат (`{count}/3`).')
                return
        if text.startswith('/start'):
            caption = '🐰🖤 **KROLIKGUARD — BETA TEST EDITION** 🖤🐰\n\n🛡️ Платная система защиты чатов от мата, спама и нарушений.\n⭐ Хочешь протестировать покупку подписки за Telegram Stars?\n\n👇 *Нажми кнопку ниже:*'
            keyboard = {'inline_keyboard': [[{'text': f'⭐ Купить PRO ({PRO_STARS_PRICE} звёзд)', 'callback_data': 'buy_stars_pro'}], [{'text': '📊 Мой статус', 'callback_data': 'check_status'}, {'text': '🛠️ Помощь', 'callback_data': 'help_info'}]]}
            TelegramProtocol.send_media(chat_id, RABBIT_GIF_URL, caption, keyboard)
            return
        if text.startswith('/paysupport'):
            TelegramProtocol.send_text(chat_id, f'Поддержка платежей: владелец, Telegram ID {OWNER_ID}. Сохраните квитанцию Telegram и номер платежа. Подписка: {PRO_STARS_PRICE} Stars на 30 дней.')
            return
        if text.startswith('/help') or text.startswith('/cmds'):
            help_msg = '🛠️ **АРСЕНАЛ КОМАНД KROLIKGUARD:**\n\n• `/ban` (ответом) — Заблокировать нарушителя\n• `/mute [время]` (ответом) — Выдать мут (`30m`, `2h`, `1d`)\n• `/unmute` (ответом) — Снять мут\n• `/status` — Телеметрия системы'
            TelegramProtocol.send_text(chat_id, help_msg)
            return
        if text.startswith('/status'):
            report = f'📊 **ТЕЛЕМЕТРИЯ KROLIKGUARD:**\n• Аптайм: `{db.get_uptime()}`\n• PRO-клиентов: `{len(db.subscribers)}`\n• Забанено в базе: `{len(db.blacklists)}`\n• Активных мутов: `{len(db.mutes)}`'
            TelegramProtocol.send_text(chat_id, report)
            return
        if text.startswith(('/grant', '/revoke')):
            if uid != OWNER_ID:
                TelegramProtocol.send_text(chat_id, f'❌ @{username}, недостаточно прав.')
                return
            parts = text.split()
            if len(parts) >= 2:
                try:
                    target_uid = int(parts[1])
                    if text.startswith('/grant'):
                        db.add_pro(target_uid, days=30)
                        TelegramProtocol.send_text(chat_id, f'✅ Пользователь `{target_uid}` получил PRO-доступ на 30 дней.')
                    elif db.remove_pro(target_uid):
                        TelegramProtocol.send_text(chat_id, f'❌ У пользователя `{target_uid}` аннулирован PRO-доступ.')
                    else:
                        TelegramProtocol.send_text(chat_id, 'ℹ️ У пользователя не было PRO-доступа.')
                except ValueError:
                    TelegramProtocol.send_text(chat_id, '❌ Неверный формат ID.')
            return
        if text.startswith(('/ban', '/mute', '/unmute')):
            if not db.is_pro(uid) or not telegram_admin(chat_id, uid):
                TelegramProtocol.send_text(chat_id, f'❌ @{username}, команды модерации доступны только по **PRO-подписке**!')
                return
            reply = msg.get('reply_to_message')
            if not reply:
                TelegramProtocol.send_text(chat_id, '⚠️ Ошибка: используй команду **ответом на сообщение** игрока.')
                return
            target_user = reply.get('from', {})
            target_id = target_user.get('id')
            target_name = target_user.get('first_name', 'Участник')
            if text.startswith('/ban'):
                db.blacklists.add(str(target_id))
                db.save()
                if chat_type in ['group', 'supergroup']:
                    TelegramProtocol.ban_member(chat_id, target_id)
                TelegramProtocol.send_text(chat_id, f'🔨 **KrolikGuard** вышвырнул нарушителя `{target_name}`.')
            elif text.startswith('/mute'):
                parts = text.split()
                time_val = self.parse_time(parts[1]) if len(parts) >= 2 else 1800
                if time_val:
                    expire = time.time() + time_val
                    db.mutes[str(target_id)] = expire
                    db.save()
                    TelegramProtocol.send_text(chat_id, f'🔇 Пользователь `{target_name}` получил мут.')
            elif text.startswith('/unmute'):
                db.mutes.pop(str(target_id), None)
                db.save()
                TelegramProtocol.send_text(chat_id, f'🔊 С пользователя `{target_name}` снят мут.')
            return

    def process_callback(self, cb):
        cb_id = cb.get('id')
        data = cb.get('data')
        msg = cb.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        user = cb.get('from', {})
        uid = user.get('id')
        TelegramProtocol.confirm_callback(cb_id)
        if data == 'buy_stars_pro':
            TelegramProtocol.send_stars_invoice(chat_id)
        elif data == 'check_status':
            is_active = 'Активна ✅ (PRO)' if db.is_pro(uid) else 'Отсутствует ❌'
            TelegramProtocol.send_text(chat_id, f'📊 Твой статус подписки в системе: **{is_active}**')
        elif data == 'help_info':
            TelegramProtocol.send_text(chat_id, '🛠️ Напиши в чате `/help`, чтобы увидеть полный список команд.')

    def start_polling(self):
        print('🚀 [TELEGRAM BOT]: Запущен в фоновом потоке...')
        while True:
            try:
                updates = TelegramProtocol.fetch_updates(self.offset)
                for update in updates:
                    self.offset = update['update_id'] + 1
                    if 'pre_checkout_query' in update:
                        q = update['pre_checkout_query']
                        ok = q.get('currency') == 'XTR' and q.get('total_amount') == PRO_STARS_PRICE and q.get('invoice_payload') == 'krolik_pro_subscription_payload'
                        packet = {'pre_checkout_query_id': q['id'], 'ok': ok}
                        if not ok: packet['error_message'] = 'Неверный счёт. Создайте новый через /start.'
                        TelegramProtocol.call_api('answerPreCheckoutQuery', packet)
                    elif 'message' in update:
                        self.process_message(update['message'])
                    elif 'callback_query' in update:
                        self.process_callback(update['callback_query'])
            except Exception:
                time.sleep(3)

def telegram_admin(chat_id, uid):
    if uid == OWNER_ID:
        return True
    try:
        url = API_URL + '/getChatMember?' + urllib.parse.urlencode({'chat_id': chat_id, 'user_id': uid})
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
        return data.get('result', {}).get('status') in ('creator', 'administrator')
    except Exception:
        return False

if __name__ == '__main__':
    # A process lock avoids two polling copies fighting for updates.
    lock = open(str(Path(__file__).resolve().parent / 'bot.lock'), 'a+')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Бот уже запущен. Закройте второе окно.')
    try:
        with urllib.request.urlopen(API_URL + '/getMe', timeout=15) as response:
            me = json.load(response)['result']
        # Polling cannot run while an old webhook is registered.
        if not TelegramProtocol.call_api('deleteWebhook', {'drop_pending_updates': False}):
            raise RuntimeError('Cannot remove webhook')
        print('Бот подключён: https://t.me/' + me['username'], flush=True)
        print('Откройте бота и отправьте /start. Оставьте это окно открытым.', flush=True)
        KrolikGuardCore().start_polling()
    except KeyboardInterrupt:
        print('Бот остановлен.')
    except Exception as exc:
        print('Не удалось подключить бота: ' + type(exc).__name__)
        print('Проверьте токен BotFather и подключение к интернету.')
