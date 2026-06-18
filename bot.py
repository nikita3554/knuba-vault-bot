"""
KNUBAVaultBot — Професійний Telegram-бот хмарного сховища
"""

import os, io, sys, time, threading, requests, telebot
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           BotCommand, ReplyKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardRemove)

# ─── Автозапуск server.py ─────────────────────────────────────────────────────
def start_server():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import server as cloud_server
        cloud_server.run_server()
    except ImportError:
        print("❌ server.py не знайдено!")
    except Exception as e:
        print(f"❌ Помилка сервера: {e}")

_server_thread = threading.Thread(target=start_server, daemon=True)
_server_thread.start()

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
    print("⚠️ Сервер не відповів за 15с, продовжуємо...")

# ─── Налаштування ─────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
SERVER_URL  = os.environ.get("SERVER_URL", "http://localhost:" + os.environ.get("PORT", "8000"))
MAX_TG_SIZE = 50 * 1024 * 1024

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ─── Встановлення команд (випадаючий список /) ────────────────────────────────
def set_bot_commands():
    commands = [
        BotCommand("start",    "🏠 Головне меню"),
        BotCommand("login",    "🔓 Увійти у сховище"),
        BotCommand("register", "📝 Реєстрація нового акаунту"),
        BotCommand("files",    "📁 Переглянути мої файли"),
        BotCommand("upload",   "📤 Інструкція завантаження"),
        BotCommand("status",   "📊 Стан системи"),
        BotCommand("profile",  "👤 Мій профіль"),
        BotCommand("help",     "❓ Допомога"),
        BotCommand("logout",   "🚪 Вийти зі сховища"),
    ]
    bot.set_my_commands(commands)

# ─── Сесії та FSM стани ─────────────────────────────────────────────────────
sessions: dict[int, dict] = {}   # uid -> {token, username}
states:   dict[int, dict] = {}   # uid -> {state, data}

# Стани FSM
STATE_LOGIN_USER    = "login_user"
STATE_LOGIN_PASS    = "login_pass"
STATE_REGISTER_USER = "register_user"
STATE_REGISTER_PASS = "register_pass"

def get_token(uid):    return sessions.get(uid, {}).get("token")
def get_username(uid): return sessions.get(uid, {}).get("username", "")
def is_logged_in(uid): return uid in sessions

def set_state(uid, state, data=None):
    states[uid] = {"state": state, "data": data or {}}

def get_state(uid):
    return states.get(uid, {}).get("state")

def get_state_data(uid):
    return states.get(uid, {}).get("data", {})

def clear_state(uid):
    states.pop(uid, None)

def kb_cancel():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("❌ Скасувати", callback_data="cancel"))
    return kb

# ─── Утиліти ──────────────────────────────────────────────────────────────────
def fmt(b):
    if b >= 1073741824: return f"{b/1073741824:.1f} GB"
    if b >= 1048576:    return f"{b/1048576:.1f} MB"
    if b >= 1024:       return f"{b/1024:.1f} KB"
    return f"{b} B"

def api(method, path, token=None, json_data=None, raw_data=None):
    url = SERVER_URL + path
    headers = {}
    if token: headers["Authorization"] = f"Bearer {token}"
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
        return {"error": "❌ Сервер недоступний"}, 0
    except Exception as e:
        return {"error": str(e)}, 0

# ─── Клавіатури ───────────────────────────────────────────────────────────────
def kb_main_auth():
    """Головне меню для авторизованого користувача."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📁 Мої файли",     callback_data="files"),
        InlineKeyboardButton("📤 Завантажити",   callback_data="how_upload"),
        InlineKeyboardButton("📊 Стан системи",  callback_data="status"),
        InlineKeyboardButton("👤 Профіль",        callback_data="profile"),
        InlineKeyboardButton("🌐 Веб-інтерфейс", callback_data="webapp"),
        InlineKeyboardButton("🚪 Вийти",          callback_data="logout"),
    )
    return kb

def kb_main_guest():
    """Головне меню для гостя."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔓 Увійти",        callback_data="guide_login"),
        InlineKeyboardButton("📝 Реєстрація",    callback_data="guide_register"),
        InlineKeyboardButton("📊 Стан системи",  callback_data="status"),
        InlineKeyboardButton("❓ Допомога",       callback_data="help"),
    )
    return kb

def kb_files(files):
    kb = InlineKeyboardMarkup(row_width=1)
    for f in files[:20]:  # максимум 20 файлів
        size = fmt(f["size_encrypted"])
        kb.add(InlineKeyboardButton(
            f"📄 {f['name']}  •  {size}",
            callback_data=f"file:{f['name'][:50]}"
        ))
    kb.add(
        InlineKeyboardButton("🔄 Оновити список", callback_data="files"),
        InlineKeyboardButton("↩️ Головне меню",   callback_data="menu"),
    )
    return kb

def kb_file_actions(filename):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬇️ Завантажити",  callback_data=f"dl:{filename[:50]}"),
        InlineKeyboardButton("🗑 Видалити",     callback_data=f"del_confirm:{filename[:50]}"),
        InlineKeyboardButton("↩️ До списку",    callback_data="files"),
    )
    return kb

def kb_confirm_delete(filename):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Так, видалити",  callback_data=f"del:{filename[:50]}"),
        InlineKeyboardButton("❌ Скасувати",      callback_data=f"file:{filename[:50]}"),
    )
    return kb

def kb_back_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("↩️ Головне меню", callback_data="menu"))
    return kb

# ─── /start ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    name = msg.from_user.first_name or "користувачу"

    if is_logged_in(uid):
        uname = get_username(uid)
        text = (f"👋 З поверненням, *{uname}*!\n\n"
                f"☁️ *KNUBAVaultBot* — ваше захищене хмарне сховище\n\n"
                f"Оберіть дію:")
        bot.send_message(uid, text, reply_markup=kb_main_auth())
    else:
        text = (f"👋 Вітаю, *{name}*!\n\n"
                f"☁️ *KNUBAVaultBot* — захищене хмарне сховище\n\n"
                f"🔐 Усі файли шифруються алгоритмом *AES-128-CBC*\n"
                f"📦 Максимальний розмір файлу: *50 MB*\n"
                f"🌐 Веб-інтерфейс: [відкрити]({SERVER_URL})\n\n"
                f"Для початку роботи — увійдіть або зареєструйтесь:")
        bot.send_message(uid, text, reply_markup=kb_main_guest(),
                        disable_web_page_preview=True)

# ─── /help ────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["help"])
def cmd_help(msg):
    text = (
        "❓ *Довідка KNUBAVaultBot*\n\n"
        "*Команди:*\n"
        "/start — головне меню\n"
        "/register `логін пароль` — реєстрація\n"
        "/login `логін пароль` — вхід\n"
        "/files — список файлів\n"
        "/upload — як завантажити файл\n"
        "/status — стан системи\n"
        "/profile — ваш профіль\n"
        "/logout — вихід\n\n"
        "*Завантаження файлів:*\n"
        "Просто надішліть будь-який файл у чат після входу — він автоматично зашифрується і збережеться.\n\n"
        "*Підтримувані типи:*\n"
        "📄 Документи · 🖼 Фото · 🎥 Відео · 🎵 Аудіо · 🎤 Голосові\n\n"
        f"🌐 Веб-інтерфейс: {SERVER_URL}"
    )
    bot.send_message(msg.from_user.id, text, reply_markup=kb_back_menu(),
                    disable_web_page_preview=True)

# ─── /register ────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["register"])
def cmd_register(msg):
    uid = msg.from_user.id
    if is_logged_in(uid):
        bot.reply_to(msg, f"⚠️ Ви вже авторизовані як *{get_username(uid)}*\n\nСпочатку /logout", reply_markup=kb_main_auth())
        return
    set_state(uid, STATE_REGISTER_USER)
    bot.reply_to(msg,
        "📝 *Реєстрація нового акаунту*\n\n"
        "Крок 1/2\n"
        "👤 Введіть бажаний логін:",
        reply_markup=kb_cancel())

# ─── /login ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["login"])
def cmd_login(msg):
    uid = msg.from_user.id
    if is_logged_in(uid):
        bot.reply_to(msg, f"⚠️ Ви вже авторизовані як *{get_username(uid)}*", reply_markup=kb_main_auth())
        return
    set_state(uid, STATE_LOGIN_USER)
    bot.reply_to(msg,
        "🔓 *Вхід у сховище*\n\n"
        "Крок 1/2\n"
        "👤 Введіть ваш логін:",
        reply_markup=kb_cancel())

# ─── /logout ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["logout"])
def cmd_logout(msg):
    uid = msg.from_user.id
    token = get_token(uid)
    if token:
        api("POST", "/logout", token=token)
        del sessions[uid]
        bot.reply_to(msg, "🚪 *Вихід виконано*\n\nДо побачення! Для повторного входу: `/login логін пароль`")
    else:
        bot.reply_to(msg, "⚠️ Ви не авторизовані.")

# ─── /files ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["files"])
def cmd_files(msg):
    uid = msg.from_user.id
    token = get_token(uid)
    if not token:
        bot.reply_to(msg, "🔒 Спочатку увійдіть: `/login логін пароль`")
        return
    _show_files(uid, msg.chat.id)

def _show_files(uid, chat_id, message_id=None):
    token = get_token(uid)
    res, code = api("GET", "/files", token=token)
    if code != 200:
        text = f"❌ {res.get('message', 'Помилка')}"
        if message_id:
            bot.edit_message_text(text, chat_id, message_id)
        else:
            bot.send_message(chat_id, text)
        return
    files = res.get("files", [])
    quota = res.get("quota", {})
    count = len(files)

    if not files:
        text = ("📂 *Сховище порожнє*\n\n"
                "Надішліть будь-який файл у чат — він автоматично збережеться.")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("↩️ Головне меню", callback_data="menu"))
    else:
        text = (f"📁 *Ваші файли* — {count} шт.\n"
                f"💾 Зайнято: {fmt(quota.get('bytes_encrypted', 0))}\n\n"
                f"Оберіть файл для дій:")
        kb = kb_files(files)

    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

# ─── /upload ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["upload"])
def cmd_upload(msg):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.reply_to(msg, "🔒 Спочатку увійдіть: `/login логін пароль`")
        return
    bot.reply_to(msg,
        "📤 *Як завантажити файл*\n\n"
        "Просто надішліть файл прямо в цей чат!\n\n"
        "✅ Підтримуються:\n"
        "• 📄 Будь-які документи\n"
        "• 🖼 Фотографії\n"
        "• 🎥 Відео\n"
        "• 🎵 Аудіо файли\n"
        "• 🎤 Голосові повідомлення\n\n"
        f"⚠️ Максимальний розмір: *50 MB*\n\n"
        "Файл буде автоматично зашифрований і збережений у сховищі.")

# ─── /status ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["status"])
def cmd_status(msg):
    res, code = api("GET", "/status")
    if code != 200:
        bot.reply_to(msg, f"❌ Сервер недоступний")
        return
    _send_status(msg.from_user.id, res)

def _send_status(uid, res, chat_id=None, message_id=None):
    sats = res.get("satellites", [])
    st   = res.get("storage_stats", {})
    ts   = res.get("timestamp", "")[:19].replace("T", " ")

    sat_lines = ""
    for s in sats:
        icon = "🟢" if s["status"] == "active" else "🔴"
        sat_lines += f"{icon} *{s['id']}* — {s.get('requests', 0)} запитів\n"

    text = (
        f"📊 *Стан системи KNUBAVault*\n\n"
        f"🕐 Оновлено: `{ts}`\n\n"
        f"*Статистика:*\n"
        f"📤 Завантажень: `{st.get('uploads', 0)}`\n"
        f"⬇️ Скачувань: `{st.get('downloads', 0)}`\n"
        f"🗑 Видалень: `{st.get('deletes', 0)}`\n"
        f"💾 Оброблено: `{fmt(st.get('total_bytes', 0))}`\n\n"
        f"*Сервери:*\n{sat_lines}\n"
        f"🔒 Rate limit: `{res.get('rate_limit', '')}`\n"
        f"📦 Макс. файл: `{fmt(res.get('max_file_size', 0))}`"
    )
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🔄 Оновити", callback_data="status"),
        InlineKeyboardButton("↩️ Меню",    callback_data="menu"),
    )
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(uid, text, reply_markup=kb)

# ─── /profile ─────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["profile"])
def cmd_profile(msg):
    uid = msg.from_user.id
    token = get_token(uid)
    if not token:
        bot.reply_to(msg, "🔒 Спочатку увійдіть: `/login логін пароль`")
        return
    _show_profile(uid, msg.chat.id)

def _show_profile(uid, chat_id, message_id=None):
    token    = get_token(uid)
    username = get_username(uid)
    res, code = api("GET", "/files", token=token)
    quota = res.get("quota", {}) if code == 200 else {}

    text = (
        f"👤 *Профіль*\n\n"
        f"👤 Логін: `{username}`\n"
        f"📁 Файлів: `{quota.get('files', 0)}`\n"
        f"💾 Зайнято: `{fmt(quota.get('bytes_encrypted', 0))}`\n"
        f"🔐 Шифрування: `AES-128-CBC (Fernet)`\n"
        f"⏱ Сесія: активна\n\n"
        f"🌐 [Веб-інтерфейс]({SERVER_URL})"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📁 Мої файли",  callback_data="files"),
        InlineKeyboardButton("↩️ Головне меню", callback_data="menu"),
    )
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=kb,
                             disable_web_page_preview=True)
    else:
        bot.send_message(chat_id, text, reply_markup=kb,
                        disable_web_page_preview=True)

# ─── Завантаження файлів ──────────────────────────────────────────────────────
@bot.message_handler(content_types=["document", "photo", "video", "audio", "voice"])
def handle_file_upload(msg):
    uid   = msg.from_user.id
    token = get_token(uid)
    if not token:
        bot.reply_to(msg,
            "🔒 *Потрібна авторизація*\n\n"
            "Увійдіть у сховище: `/login логін пароль`")
        return

    if msg.document:
        file_info = msg.document
        filename  = file_info.file_name or f"file_{int(time.time())}"
        file_size = file_info.file_size or 0
    elif msg.photo:
        file_info = msg.photo[-1]
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

    if file_size > MAX_TG_SIZE:
        bot.reply_to(msg,
            f"❌ *Файл завеликий*\n\n"
            f"Розмір: `{fmt(file_size)}`\n"
            f"Ліміт Telegram: `{fmt(MAX_TG_SIZE)}`")
        return

    progress_msg = bot.reply_to(msg,
        f"⏳ *Зберігаю* `{filename}`...\n"
        f"📦 Розмір: `{fmt(file_size)}`")
    try:
        tg_file   = bot.get_file(file_info.file_id)
        file_data = bot.download_file(tg_file.file_path)
        res, code = api("POST", f"/upload/{filename}", token=token, raw_data=file_data)

        if code == 200:
            bot.edit_message_text(
                f"✅ *Файл збережено!*\n\n"
                f"📄 Назва: `{filename}`\n"
                f"📦 Оригінал: `{fmt(res.get('size', 0))}`\n"
                f"🔐 Зашифровано: `{fmt(res.get('size_enc', 0))}`\n"
                f"🖥 Сервер: `{res.get('routed_via', '?')}`\n"
                f"🔑 MD5: `{res.get('md5', '')[:16]}...`",
                msg.chat.id, progress_msg.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 Всі файли", callback_data="files"),
                    InlineKeyboardButton("↩️ Меню",      callback_data="menu"),
                ))
        else:
            bot.edit_message_text(
                f"❌ *Помилка збереження*\n\n{res.get('message', 'Невідома помилка')}",
                msg.chat.id, progress_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Помилка: {e}", msg.chat.id, progress_msg.message_id)

# ─── Callback кнопки ──────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid  = call.from_user.id
    data = call.data
    cid  = call.message.chat.id
    mid  = call.message.message_id
    token = get_token(uid)

    bot.answer_callback_query(call.id)

    # Головне меню
    if data == "menu":
        if is_logged_in(uid):
            uname = get_username(uid)
            bot.edit_message_text(
                f"👤 *{uname}* | ☁️ KNUBAVaultBot\n\nОберіть дію:",
                cid, mid, reply_markup=kb_main_auth())
        else:
            bot.edit_message_text(
                "☁️ *KNUBAVaultBot*\n\nОберіть дію:",
                cid, mid, reply_markup=kb_main_guest())

    # Список файлів
    elif data == "files":
        if not token:
            bot.answer_callback_query(call.id, "🔒 Спочатку увійдіть!", show_alert=True)
            return
        _show_files(uid, cid, mid)

    # Інфо про файл
    elif data.startswith("file:"):
        filename = data[5:]
        bot.edit_message_text(
            f"📄 *{filename}*\n\nОберіть дію:",
            cid, mid, reply_markup=kb_file_actions(filename))

    # Підтвердження видалення
    elif data.startswith("del_confirm:"):
        filename = data[12:]
        bot.edit_message_text(
            f"🗑 *Видалити файл?*\n\n`{filename}`\n\n⚠️ Цю дію неможливо скасувати!",
            cid, mid, reply_markup=kb_confirm_delete(filename))

    # Видалення
    elif data.startswith("del:"):
        filename = data[4:]
        if not token: return
        res, code = api("DELETE", f"/delete/{filename}", token=token)
        if code == 200:
            bot.edit_message_text(
                f"🗑 *Файл видалено*\n\n`{filename}`",
                cid, mid,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 До списку", callback_data="files"),
                    InlineKeyboardButton("↩️ Меню",      callback_data="menu"),
                ))
        else:
            bot.answer_callback_query(call.id, f"❌ {res.get('message')}", show_alert=True)

    # Скачати файл
    elif data.startswith("dl:"):
        filename = data[3:]
        if not token: return
        bot.send_message(uid, f"⏳ Отримую `{filename}`...")
        raw, code = api("GET", f"/download/{filename}", token=token)
        if code == 200 and isinstance(raw, bytes):
            bot.send_document(uid,
                (filename, io.BytesIO(raw), "application/octet-stream"),
                caption=f"📄 `{filename}` | {fmt(len(raw))}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 До списку", callback_data="files")
                ))
        else:
            err = raw.get("message", "Помилка") if isinstance(raw, dict) else "Помилка"
            bot.send_message(uid, f"❌ {err}")

    # Стан системи
    elif data == "status":
        res, code = api("GET", "/status")
        if code == 200:
            _send_status(uid, res, cid, mid)
        else:
            bot.answer_callback_query(call.id, "❌ Сервер недоступний", show_alert=True)

    # Профіль
    elif data == "profile":
        if not token:
            bot.answer_callback_query(call.id, "🔒 Спочатку увійдіть!", show_alert=True)
            return
        _show_profile(uid, cid, mid)

    # Веб-інтерфейс
    elif data == "webapp":
        bot.edit_message_text(
            f"🌐 *Веб-інтерфейс*\n\n"
            f"Відкрийте у браузері:\n{SERVER_URL}\n\n"
            f"Там можна керувати файлами через зручний веб-інтерфейс.",
            cid, mid,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🌐 Відкрити", url=SERVER_URL),
                InlineKeyboardButton("↩️ Меню",     callback_data="menu"),
            ),
            disable_web_page_preview=True)

    # Інструкції
    elif data == "how_upload":
        bot.edit_message_text(
            "📤 *Як завантажити файл*\n\n"
            "Просто надішліть файл у цей чат!\n\n"
            "✅ Підтримуються:\n"
            "• 📄 Документи\n• 🖼 Фото\n• 🎥 Відео\n• 🎵 Аудіо\n• 🎤 Голосові\n\n"
            f"⚠️ Ліміт: *50 MB*",
            cid, mid, reply_markup=kb_back_menu())

    elif data == "help":
        bot.edit_message_text(
            "❓ *Довідка*\n\n"
            "/start — головне меню\n"
            "/register `логін пароль` — реєстрація\n"
            "/login `логін пароль` — вхід\n"
            "/files — список файлів\n"
            "/upload — як завантажити\n"
            "/status — стан системи\n"
            "/profile — ваш профіль\n"
            "/logout — вийти",
            cid, mid, reply_markup=kb_back_menu())

    elif data == "logout":
        if token:
            api("POST", "/logout", token=token)
            del sessions[uid]
        clear_state(uid)
        bot.edit_message_text(
            "🚪 *Вихід виконано*\n\nДо побачення! Натисніть /login щоб увійти знову.",
            cid, mid, reply_markup=kb_main_guest())

    # Скасування FSM
    elif data == "cancel":
        clear_state(uid)
        if is_logged_in(uid):
            bot.edit_message_text("↩️ Скасовано.", cid, mid, reply_markup=kb_main_auth())
        else:
            bot.edit_message_text("↩️ Скасовано.", cid, mid, reply_markup=kb_main_guest())

    # Кнопки входу/реєстрації з меню
    elif data == "guide_login":
        clear_state(uid)
        set_state(uid, STATE_LOGIN_USER)
        bot.edit_message_text(
            "🔓 *Вхід у сховище*\n\nКрок 1/2\n👤 Введіть ваш логін:",
            cid, mid, reply_markup=kb_cancel())

    elif data == "guide_register":
        clear_state(uid)
        set_state(uid, STATE_REGISTER_USER)
        bot.edit_message_text(
            "📝 *Реєстрація*\n\nКрок 1/2\n👤 Введіть бажаний логін:",
            cid, mid, reply_markup=kb_cancel())

# ─── Текстові повідомлення + FSM ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    uid   = msg.from_user.id
    text  = msg.text.strip() if msg.text else ""
    state = get_state(uid)

    # ── FSM: Вхід ────────────────────────────────────────────────────────────
    if state == STATE_LOGIN_USER:
        set_state(uid, STATE_LOGIN_PASS, {"username": text})
        bot.reply_to(msg,
            f"👤 Логін: `{text}`\n\n"
            "Крок 2/2\n"
            "🔑 Введіть пароль:",
            reply_markup=kb_cancel())
        return

    if state == STATE_LOGIN_PASS:
        data     = get_state_data(uid)
        username = data.get("username", "")
        password = text
        clear_state(uid)
        # Видаляємо повідомлення з паролем
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        res, code = api("POST", "/login", json_data={"username": username, "password": password})
        if code == 200:
            sessions[uid] = {"token": res["token"], "username": username}
            bot.send_message(uid,
                f"✅ *Вхід виконано!*\n\n"
                f"👤 Акаунт: *{username}*\n"
                f"🔐 Сесія активна 24 години\n\n"
                f"Оберіть дію:",
                reply_markup=kb_main_auth())
        else:
            bot.send_message(uid,
                f"❌ *Помилка входу*\n\n"
                f"{res.get('message', 'Невірний логін або пароль')}\n\n"
                f"Спробуйте ще раз /login",
                reply_markup=kb_main_guest())
        return

    # ── FSM: Реєстрація ───────────────────────────────────────────────────────
    if state == STATE_REGISTER_USER:
        if len(text) < 3:
            bot.reply_to(msg, "⚠️ Логін має бути мінімум 3 символи. Спробуйте ще:", reply_markup=kb_cancel())
            return
        set_state(uid, STATE_REGISTER_PASS, {"username": text})
        bot.reply_to(msg,
            f"👤 Логін: `{text}`\n\n"
            "Крок 2/2\n"
            "🔑 Введіть пароль (мін. 4 символи):",
            reply_markup=kb_cancel())
        return

    if state == STATE_REGISTER_PASS:
        data     = get_state_data(uid)
        username = data.get("username", "")
        password = text
        clear_state(uid)
        if len(password) < 4:
            bot.reply_to(msg, "⚠️ Пароль має бути мінімум 4 символи. Почніть знову /register", reply_markup=kb_main_guest())
            return
        # Видаляємо повідомлення з паролем
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        res, code = api("POST", "/register", json_data={"username": username, "password": password})
        if code == 201:
            bot.send_message(uid,
                f"✅ *Акаунт створено!*\n\n"
                f"👤 Логін: `{username}`\n\n"
                f"Тепер увійдіть — натисніть /login",
                reply_markup=kb_main_guest())
        elif code == 409:
            bot.send_message(uid,
                f"⚠️ Логін `{username}` вже зайнятий.\n\nСпробуйте інший /register",
                reply_markup=kb_main_guest())
        else:
            bot.send_message(uid, f"❌ {res.get('message', 'Помилка')}")
        return

    # ── Звичайні повідомлення ─────────────────────────────────────────────────
    if is_logged_in(uid):
        bot.reply_to(msg,
            "📤 Щоб завантажити файл — надішліть його як вкладення.\n\n"
            "Або оберіть дію:",
            reply_markup=kb_main_auth())
    else:
        bot.reply_to(msg,
            "🔒 Ви не авторизовані.\n\n"
            "Натисніть /start або оберіть дію:",
            reply_markup=kb_main_guest())

# ─── Запуск ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    set_bot_commands()
    print("=" * 50)
    print("  KNUBAVaultBot запущено")
    print(f"  Сервер: {SERVER_URL}")
    print("=" * 50)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
