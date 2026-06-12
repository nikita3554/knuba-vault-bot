"""
KNUBAVaultBot — Telegram-бот для керування хмарним сховищем
Підключається до server.py через HTTP API

Встановлення:
    pip install pyTelegramBotAPI requests

Запуск:
    1. Отримайте токен бота у @BotFather
    2. Вставте токен у BOT_TOKEN нижче
    3. python bot.py  (server.py має бути запущений)
"""

import os
import io
import sys
import time
import threading
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ─── Автозапуск server.py у фоновому потоці ───────────────────────────────────
def start_server():
    """Запускає server.py в тому самому процесі у фоні."""
    # Додаємо поточну папку до шляху пошуку модулів
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import server as cloud_server
        print("✅ Сервер хмарного сховища запущено на порту 8000")
        cloud_server.run_server(port=8000)
    except ImportError:
        print("❌ Файл server.py не знайдено поряд з bot.py!")
    except Exception as e:
        print(f"❌ Помилка запуску сервера: {e}")

# Запускаємо сервер у демон-потоці (зупиниться разом з ботом)
_server_thread = threading.Thread(target=start_server, daemon=True)
_server_thread.start()

# Чекаємо поки сервер реально запуститься
print("⏳ Очікування запуску сервера...")
for _ in range(15):
    time.sleep(1)
    try:
        r = requests.get("http://localhost:8000/status", timeout=2)
        if r.status_code == 200:
            print("✅ Сервер готовий!")
            break
    except Exception:
        pass
else:
    print("⚠️ Сервер не відповів за 15с, але продовжуємо...")

# ─── Налаштування ─────────────────────────────────────────────────────────────
BOT_TOKEN    = "ВАШ_НОВИЙ_ТОКЕН_ВІД_BOTFATHER"   # отримати у @BotFather
SERVER_URL   = "http://localhost:8000"      # адреса server.py
MAX_TG_SIZE  = 50 * 1024 * 1024            # Telegram ліміт: 50 MB на файл

bot = telebot.TeleBot(BOT_TOKEN)

# ─── Зберігаємо токени сесій: {telegram_user_id: server_token} ───────────────
sessions: dict[int, str] = {}


# ─── Утиліти ──────────────────────────────────────────────────────────────────
def fmt(b: int) -> str:
    if b >= 1073741824: return f"{b/1073741824:.1f} GB"
    if b >= 1048576:    return f"{b/1048576:.1f} MB"
    if b >= 1024:       return f"{b/1024:.1f} KB"
    return f"{b} B"

def api(method: str, path: str, token: str = None,
        json_data=None, raw_data: bytes = None) -> tuple[dict | bytes, int]:
    """Запит до server.py."""
    url     = SERVER_URL + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=30)
        elif method == "POST" and raw_data is not None:
            headers["Content-Type"] = "application/octet-stream"
            r = requests.post(url, headers=headers, data=raw_data, timeout=60)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=json_data, timeout=10)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=10)
        else:
            return {"error": "Unknown method"}, 0

        if r.headers.get("Content-Type", "").startswith("application/json"):
            return r.json(), r.status_code
        return r.content, r.status_code
    except requests.exceptions.ConnectionError:
        return {"error": "❌ Сервер недоступний. Запустіть python server.py"}, 0
    except Exception as e:
        return {"error": str(e)}, 0

def get_token(uid: int) -> str | None:
    return sessions.get(uid)

def is_logged_in(uid: int) -> bool:
    return uid in sessions

def main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📁 Мої файли",    callback_data="files"),
        InlineKeyboardButton("📊 Стан системи", callback_data="status"),
        InlineKeyboardButton("🚪 Вийти",         callback_data="logout"),
    )
    return kb

def files_keyboard(files: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for f in files:
        kb.add(InlineKeyboardButton(
            f"📄 {f['name']}  ({fmt(f['size_encrypted'])} enc)",
            callback_data=f"file:{f['name']}"
        ))
    kb.add(InlineKeyboardButton("↩️ Назад", callback_data="menu"))
    return kb

def file_keyboard(filename: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬇️ Скачати",  callback_data=f"dl:{filename}"),
        InlineKeyboardButton("🗑 Видалити", callback_data=f"del:{filename}"),
        InlineKeyboardButton("↩️ До списку", callback_data="files"),
    )
    return kb


# ─── /start ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_start(msg):
    uid = msg.from_user.id
    if is_logged_in(uid):
        bot.send_message(uid,
            "👋 Ви вже авторизовані!\n\nОберіть дію:",
            reply_markup=main_keyboard())
        return

    bot.send_message(uid,
        "☁️ *Хмарне сховище КНУБА*\n\n"
        "Щоб почати — авторизуйтесь:\n"
        "```\n/login логін пароль\n```\n\n"
        "Або зареєструйтесь:\n"
        "```\n/register логін пароль\n```\n\n"
        "📌 Команди:\n"
        "/login — увійти\n"
        "/register — реєстрація\n"
        "/files — список файлів\n"
        "/status — стан системи\n"
        "/logout — вийти\n\n"
        "📤 Щоб завантажити файл — просто надішліть його у чат після входу.",
        parse_mode="Markdown")


# ─── /register ────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["register"])
def cmd_register(msg):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(msg, "❌ Використання: `/register логін пароль`",
                     parse_mode="Markdown")
        return
    _, username, password = parts
    res, code = api("POST", "/register", json_data={"username": username, "password": password})
    if code == 201:
        bot.reply_to(msg, f"✅ Користувача `{username}` зареєстровано!\n\nТепер: `/login {username} {password}`",
                     parse_mode="Markdown")
    else:
        bot.reply_to(msg, f"❌ {res.get('message', 'Помилка')}")


# ─── /login ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["login"])
def cmd_login(msg):
    parts = msg.text.split(maxsplit=2)
    uid   = msg.from_user.id
    if len(parts) < 3:
        bot.reply_to(msg, "❌ Використання: `/login логін пароль`",
                     parse_mode="Markdown")
        return
    _, username, password = parts
    res, code = api("POST", "/login", json_data={"username": username, "password": password})
    if code == 200:
        sessions[uid] = res["token"]
        # Видаляємо повідомлення з паролем для безпеки
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass
        bot.send_message(uid,
            f"✅ Вхід виконано, *{username}*!\n\nОберіть дію:",
            parse_mode="Markdown",
            reply_markup=main_keyboard())
    else:
        bot.reply_to(msg, f"❌ {res.get('message', 'Невірний логін або пароль')}")


# ─── /logout ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["logout"])
def cmd_logout(msg):
    uid   = msg.from_user.id
    token = get_token(uid)
    if token:
        api("POST", "/logout", token=token)
        del sessions[uid]
    bot.reply_to(msg, "👋 Ви вийшли зі сховища.")


# ─── /files ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["files"])
def cmd_files(msg):
    uid   = msg.from_user.id
    token = get_token(uid)
    if not token:
        bot.reply_to(msg, "🔒 Спочатку виконайте `/login`", parse_mode="Markdown")
        return
    res, code = api("GET", "/files", token=token)
    if code != 200:
        bot.reply_to(msg, f"❌ {res.get('message', 'Помилка')}")
        return
    files = res.get("files", [])
    quota = res.get("quota", {})
    if not files:
        bot.reply_to(msg, "📂 Сховище порожнє.\n\nНадішліть файл у чат, щоб завантажити.")
        return
    text = (f"📁 *Ваші файли* ({len(files)} шт.)\n"
            f"💾 Зайнято: {fmt(quota.get('bytes_encrypted', 0))}\n\n"
            "Натисніть на файл для дій:")
    bot.send_message(uid, text, parse_mode="Markdown",
                     reply_markup=files_keyboard(files))


# ─── /status ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["status"])
def cmd_status(msg):
    res, code = api("GET", "/status")
    if code != 200:
        bot.reply_to(msg, f"❌ {res.get('error', 'Сервер недоступний')}")
        return
    sats = res.get("satellites", [])
    st   = res.get("storage_stats", {})
    text = (
        "📊 *Стан системи*\n\n"
        f"🕐 Час: `{res.get('timestamp','')[:19]}`\n"
        f"📤 Завантажено: `{st.get('uploads', 0)}` файлів\n"
        f"💾 Об'єм: `{fmt(st.get('total_bytes', 0))}`\n"
        f"🔒 Rate limit: `{res.get('rate_limit','')}`\n"
        f"📦 Макс. файл: `{res.get('max_file_size', 0) // (1024*1024)} MB`\n\n"
    )
    for s in sats:
        icon = "🟢" if s["status"] == "active" else "🔴"
        text += f"{icon} {s['id']}: `{s['status']}` | запитів: `{s.get('requests', 0)}`\n"
    bot.reply_to(msg, text, parse_mode="Markdown")


# ─── Отримання файлу від користувача (upload) ─────────────────────────────────
@bot.message_handler(content_types=["document", "photo", "video", "audio", "voice"])
def handle_file_upload(msg):
    uid   = msg.from_user.id
    token = get_token(uid)
    if not token:
        bot.reply_to(msg, "🔒 Спочатку виконайте `/login`", parse_mode="Markdown")
        return

    # Визначаємо тип файлу
    if msg.document:
        file_info = msg.document
        filename  = file_info.file_name or f"file_{int(time.time())}"
        file_size = file_info.file_size
    elif msg.photo:
        file_info = msg.photo[-1]           # найбільша якість
        filename  = f"photo_{int(time.time())}.jpg"
        file_size = file_info.file_size or 0
    elif msg.video:
        file_info = msg.video
        filename  = msg.video.file_name or f"video_{int(time.time())}.mp4"
        file_size = file_info.file_size or 0
    elif msg.audio:
        file_info = msg.audio
        filename  = msg.audio.file_name or f"audio_{int(time.time())}.mp3"
        file_size = file_info.file_size or 0
    elif msg.voice:
        file_info = msg.voice
        filename  = f"voice_{int(time.time())}.ogg"
        file_size = file_info.file_size or 0
    else:
        return

    if file_size and file_size > MAX_TG_SIZE:
        bot.reply_to(msg, f"❌ Файл завеликий для Telegram ({fmt(file_size)}). Ліміт: {fmt(MAX_TG_SIZE)}")
        return

    progress_msg = bot.reply_to(msg, f"⏳ Завантаження `{filename}`...", parse_mode="Markdown")

    try:
        # Скачуємо з Telegram
        tg_file   = bot.get_file(file_info.file_id)
        file_data = bot.download_file(tg_file.file_path)

        # Відправляємо на сервер
        res, code = api("POST", f"/upload/{filename}", token=token, raw_data=file_data)

        if code == 200:
            bot.edit_message_text(
                f"✅ *{filename}* збережено!\n"
                f"📦 Розмір: `{fmt(res.get('size', 0))}`\n"
                f"🔐 Зашифровано: `{fmt(res.get('size_enc', 0))}`\n"
                f"🖥 Через: `{res.get('routed_via', '?')}`",
                msg.chat.id, progress_msg.message_id,
                parse_mode="Markdown")
        else:
            bot.edit_message_text(
                f"❌ Помилка: {res.get('message', 'Невідома помилка')}",
                msg.chat.id, progress_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Помилка: {e}", msg.chat.id, progress_msg.message_id)


# ─── Callback-кнопки ──────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid   = call.from_user.id
    token = get_token(uid)
    data  = call.data

    bot.answer_callback_query(call.id)

    # Головне меню
    if data == "menu":
        bot.edit_message_text("Оберіть дію:", call.message.chat.id,
                              call.message.message_id, reply_markup=main_keyboard())

    # Список файлів
    elif data == "files":
        if not token:
            bot.answer_callback_query(call.id, "🔒 Спочатку увійдіть!", show_alert=True)
            return
        res, code = api("GET", "/files", token=token)
        if code != 200:
            bot.edit_message_text(f"❌ {res.get('message')}", call.message.chat.id, call.message.message_id)
            return
        files = res.get("files", [])
        quota = res.get("quota", {})
        if not files:
            bot.edit_message_text("📂 Сховище порожнє.", call.message.chat.id, call.message.message_id,
                                  reply_markup=InlineKeyboardMarkup().add(
                                      InlineKeyboardButton("↩️ Назад", callback_data="menu")))
            return
        text = (f"📁 *Файли* ({len(files)} шт.) | {fmt(quota.get('bytes_encrypted',0))}\n"
                "Оберіть файл:")
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=files_keyboard(files))

    # Інфо про файл
    elif data.startswith("file:"):
        filename = data[5:]
        bot.edit_message_text(
            f"📄 *{filename}*\n\nОберіть дію:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=file_keyboard(filename))

    # Скачати файл
    elif data.startswith("dl:"):
        filename = data[3:]
        if not token:
            return
        bot.send_message(uid, f"⏳ Отримання `{filename}`...", parse_mode="Markdown")
        raw, code = api("GET", f"/download/{filename}", token=token)
        if code == 200 and isinstance(raw, bytes):
            bot.send_document(uid, (filename, io.BytesIO(raw), "application/octet-stream"),
                              caption=f"📄 `{filename}` | {fmt(len(raw))}",
                              parse_mode="Markdown")
        else:
            err = raw.get("message", "Помилка") if isinstance(raw, dict) else "Помилка"
            bot.send_message(uid, f"❌ {err}")

    # Видалити файл
    elif data.startswith("del:"):
        filename = data[4:]
        if not token:
            return
        res, code = api("DELETE", f"/delete/{filename}", token=token)
        if code == 200:
            bot.edit_message_text(f"🗑 Файл `{filename}` видалено.",
                                  call.message.chat.id, call.message.message_id,
                                  parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup().add(
                                      InlineKeyboardButton("↩️ До списку", callback_data="files")))
        else:
            bot.answer_callback_query(call.id, f"❌ {res.get('message')}", show_alert=True)

    # Стан системи
    elif data == "status":
        res, code = api("GET", "/status")
        if code == 200:
            st = res.get("storage_stats", {})
            sats = res.get("satellites", [])
            lines = [f"📊 *Система активна*",
                     f"📤 Файлів: `{st.get('uploads',0)}`",
                     f"💾 Об'єм: `{fmt(st.get('total_bytes',0))}`"]
            for s in sats:
                icon = "🟢" if s["status"] == "active" else "🔴"
                lines.append(f"{icon} {s['id']}: `{s.get('requests',0)}` запитів")
            bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id,
                                  parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup().add(
                                      InlineKeyboardButton("↩️ Назад", callback_data="menu")))

    # Logout
    elif data == "logout":
        if token:
            api("POST", "/logout", token=token)
            del sessions[uid]
        bot.edit_message_text("👋 Ви вийшли.", call.message.chat.id, call.message.message_id)


# ─── Текстові повідомлення (не команди) ──────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    uid = msg.from_user.id
    if is_logged_in(uid):
        bot.reply_to(msg,
            "📌 Щоб завантажити файл — надішліть його як вкладення.\n\n"
            "Команди: /files /status /logout",
            reply_markup=main_keyboard())
    else:
        bot.reply_to(msg,
            "🔒 Ви не авторизовані.\n\n"
            "Введіть: `/login логін пароль`",
            parse_mode="Markdown")


# ─── Запуск ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  KNUBAVaultBot запущено")
    print(f"  Сервер: {SERVER_URL}")
    print("  Запуск: python bot.py  (сервер вбудований)")
    print("=" * 50)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
