import getpass
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse


def main():
    print('Подключение Telegram-бота к Vercel. Токен бота здесь вводить не нужно.')
    site = input('Адрес проекта (https://имя.vercel.app): ').strip().rstrip('/')
    parsed = urlparse(site)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
        print('Нужен HTTPS-адрес проекта без пути.'); return
    secret = getpass.getpass('WEBHOOK_SECRET из переменных Vercel (ввод скрыт): ').strip()
    for endpoint in ('connect','diagnostics'):
        req = urllib.request.Request(site+'/'+endpoint, data=b'{}' if endpoint == 'connect' else None, headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'},method='POST' if endpoint == 'connect' else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 403: print('Доступ запрещён: проверьте WEBHOOK_SECRET и Deployment Protection в Vercel.')
            elif exc.code == 404: print('Обработчик не найден. Загрузите ВСЕ файлы проекта, app.py должен лежать в корне.')
            else: print('Сервер вернул ошибку. Проверьте переменные и Runtime Logs в Vercel.')
            return
        except Exception:
            print('Не удалось связаться с сервером. Проверьте адрес и интернет.'); return
        if not result.get('ok'):
            print('Подключение не завершено. Проверьте Runtime Logs в Vercel.'); return
        if endpoint == 'connect':
            print('Webhook подключён: '+str(result.get('webhook')))
            print('Бот: https://t.me/'+str(result.get('bot')))
        elif result.get('last_error'):
            print('Последняя ошибка Telegram: '+str(result['last_error']))
    print('Отправьте боту /start в Telegram. После подключения компьютер можно выключить.')

if __name__ == '__main__':
    try: main()
    except (KeyboardInterrupt, EOFError): print('\nОтменено.')
