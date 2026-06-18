"""
KNUBAVaultBot — Професійний Telegram-бот хмарного сховища
"""

import os, io, sys, time, threading, requests, telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

# ─── Автозапуск server.py ─────────────────────────────────────────────────────
def start_server():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import server as cloud_server
        cloud_server.run_server()
    except ImportError:
        print("server.py не знайдено!")
    except Exception as e:
        print(f"Помилка сервера: {e}")

threading.Thread(target=start_server, daemon=True).start()

print("Очікування сервера...")
for _ in range(15):
    time.sleep(1)
    try:
        if requests.get("http://localhost:8000/status", timeout=2).status_code == 200:
            print("Сервер готовий!")
            break
    except:
        pass

# ─── Налаштування ─────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
SERVER_URL = "http://localhost:" + os.environ.get("PORT", "8000")
MAX_SIZE   = 50 * 1024 * 1024

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ─── Стани ───────────────────────────────────────────────────────────────────
sessions = {}   # uid -> {token, username}
states   = {}   # uid -> {state, data}

S_LOGIN_USER    = "login_user"
S_LOGIN_PASS    = "login_pass"
S_REG_USER      = "reg_user"
S_REG_PASS      = "reg_pass"

def tok(uid):      return sessions.get(uid, {}).get("token")
def uname(uid):    return sessions.get(uid, {}).get("username", "")
def logged(uid):   return uid in sessions
def state(uid):    return states.get(uid, {}).get("state")
def sdata(uid):    return states.get(uid, {}).get("data", {})
def setst(uid, s, d=None): states[uid] = {"state": s, "data": d or {}}
def clrst(uid):    states.pop(uid, None)

# ─── API ──────────────────────────────────────────────────────────────────────
def api(method, path, token=None, json_data=None, raw=None):
    url = SERVER_URL + path
    h = {}
    if token: h["Authorization"] = f"Bearer {token}"
    try:
        if method == "GET":
            r = requests.get(url, headers=h, timeout=30)
        elif method == "POST" and raw:
            h["Content-Type"] = "application/octet-stream"
            r = requests.post(url, headers=h, data=raw, timeout=60)
        elif method == "POST":
            r = requests.post(url, headers=h, json=json_data, timeout=10)
        else:
            r = requests.delete(url, headers=h, timeout=10)
        if "application/json" in r.headers.get("Content-Type",""):
            return r.json(), r.status_code
        return r.content, r.status_code
    except:
        return {"error": "Сервер недоступний"}, 0

# ─── Розміри ──────────────────────────────────────────────────────────────────
def fmt(b):
    if b>=1073741824: return f"{b/1073741824:.1f} GB"
    if b>=1048576:    return f"{b/1048576:.1f} MB"
    if b>=1024:       return f"{b/1024:.1f} KB"
    return f"{b} B"

# ─── Клавіатури ───────────────────────────────────────────────────────────────
def kb_guest():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔓 Увійти",       callback_data="do_login"),
        InlineKeyboardButton("📝 Реєстрація",   callback_data="do_register"),
        InlineKeyboardButton("📊 Стан системи", callback_data="status"),
        InlineKeyboardButton("❓ Допомога",      callback_data="help"),
    )
    return kb

def kb_user():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📁 Мої файли",     callback_data="files"),
        InlineKeyboardButton("📊 Стан системи",  callback_data="status"),
        InlineKeyboardButton("👤 Профіль",        callback_data="profile"),
        InlineKeyboardButton("🌐 Веб-інтерфейс", callback_data="webapp"),
        InlineKeyboardButton("🚪 Вийти",          callback_data="logout"),
    )
    return kb

def kb_cancel():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("❌ Скасувати", callback_data="cancel"))
    return kb

def kb_back():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("↩️ Назад", callback_data="menu"))
    return kb

def kb_files(files):
    kb = InlineKeyboardMarkup(row_width=1)
    for f in files[:20]:
        kb.add(InlineKeyboardButton(
            f"📄 {f['name']}  •  {fmt(f['size_encrypted'])}",
            callback_data=f"file:{f['name'][:50]}"
        ))
    kb.add(
        InlineKeyboardButton("🔄 Оновити", callback_data="files"),
        InlineKeyboardButton("↩️ Меню",    callback_data="menu"),
    )
    return kb

def kb_file(name):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬇️ Скачати",   callback_data=f"dl:{name[:50]}"),
        InlineKeyboardButton("🗑 Видалити",  callback_data=f"del_ask:{name[:50]}"),
        InlineKeyboardButton("↩️ До списку", callback_data="files"),
    )
    return kb

def kb_del_confirm(name):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Так, видалити", callback_data=f"del:{name[:50]}"),
        InlineKeyboardButton("❌ Скасувати",     callback_data=f"file:{name[:50]}"),
    )
    return kb

# ─── Команди ──────────────────────────────────────────────────────────────────
def set_commands():
    bot.set_my_commands([
        BotCommand("start",    "🏠 Головне меню"),
        BotCommand("login",    "🔓 Увійти у сховище"),
        BotCommand("register", "📝 Реєстрація"),
        BotCommand("files",    "📁 Мої файли"),
        BotCommand("profile",  "👤 Мій профіль"),
        BotCommand("status",   "📊 Стан системи"),
        BotCommand("help",     "❓ Допомога"),
        BotCommand("logout",   "🚪 Вийти"),
    ])

# ─── /start ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "користувачу"
    clrst(uid)
    if logged(uid):
        bot.send_message(uid,
            f"👋 З поверненням, *{uname(uid)}*!\n\nОберіть дію:",
            reply_markup=kb_user())
    else:
        bot.send_message(uid,
            f"👋 Вітаю, *{name}*!\n\n"
            f"☁️ *KNUBAVaultBot* — захищене хмарне сховище\n\n"
            f"🔐 Шифрування: *AES-128-CBC*\n"
            f"📦 Ліміт файлу: *50 MB*\n\n"
            f"Оберіть дію:",
            reply_markup=kb_guest())

# ─── /help ────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["help"])
def cmd_help(msg):
    bot.send_message(msg.from_user.id,
        "❓ *Довідка KNUBAVaultBot*\n\n"
        "/start — головне меню\n"
        "/login — увійти у сховище\n"
        "/register — реєстрація\n"
        "/files — список файлів\n"
        "/profile — ваш профіль\n"
        "/status — стан системи\n"
        "/logout — вийти\n\n"
        "📤 *Завантаження файлів:*\n"
        "Надішліть будь-який файл у чат після входу.",
        reply_markup=kb_back())

# ─── /login ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["login"])
def cmd_login(msg):
    uid = msg.from_user.id
    clrst(uid)
    if logged(uid):
        bot.reply_to(msg, f"✅ Ви вже авторизовані як *{uname(uid)}*", reply_markup=kb_user())
        return
    setst(uid, S_LOGIN_USER)
    bot.reply_to(msg,
        "🔓 *Вхід у сховище*\n\n"
        "Крок 1 з 2\n"
        "👤 Введіть ваш логін:",
        reply_markup=kb_cancel())

# ─── /register ────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["register"])
def cmd_register(msg):
    uid = msg.from_user.id
    clrst(uid)
    if logged(uid):
        bot.reply_to(msg, f"✅ Ви вже авторизовані як *{uname(uid)}*", reply_markup=kb_user())
        return
    setst(uid, S_REG_USER)
    bot.reply_to(msg,
        "📝 *Реєстрація нового акаунту*\n\n"
        "Крок 1 з 2\n"
        "👤 Введіть бажаний логін:",
        reply_markup=kb_cancel())

# ─── /logout ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["logout"])
def cmd_logout(msg):
    uid = msg.from_user.id
    clrst(uid)
    if tok(uid):
        api("POST", "/logout", token=tok(uid))
        del sessions[uid]
    bot.reply_to(msg,
        "🚪 *Вихід виконано*\n\nДо побачення! Натисніть /start",
        reply_markup=kb_guest())

# ─── /files ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["files"])
def cmd_files(msg):
    uid = msg.from_user.id
    if not logged(uid):
        bot.reply_to(msg, "🔒 Спочатку увійдіть: /login")
        return
    show_files(uid, msg.chat.id)

# ─── /status ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["status"])
def cmd_status(msg):
    res, code = api("GET", "/status")
    if code != 200:
        bot.reply_to(msg, "❌ Сервер недоступний")
        return
    send_status(msg.from_user.id, res)

# ─── /profile ─────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["profile"])
def cmd_profile(msg):
    uid = msg.from_user.id
    if not logged(uid):
        bot.reply_to(msg, "🔒 Спочатку увійдіть: /login")
        return
    show_profile(uid, msg.chat.id)

# ─── Завантаження файлів ──────────────────────────────────────────────────────
@bot.message_handler(content_types=["document","photo","video","audio","voice"])
def handle_file(msg):
    uid = msg.from_user.id
    if not logged(uid):
        bot.reply_to(msg, "🔒 Спочатку увійдіть: /login")
        return

    if msg.document:
        fi = msg.document; fn = fi.file_name or f"file_{int(time.time())}"; fs = fi.file_size or 0
    elif msg.photo:
        fi = msg.photo[-1]; fn = f"photo_{int(time.time())}.jpg"; fs = fi.file_size or 0
    elif msg.video:
        fi = msg.video; fn = fi.file_name or f"video_{int(time.time())}.mp4"; fs = fi.file_size or 0
    elif msg.audio:
        fi = msg.audio; fn = fi.file_name or f"audio_{int(time.time())}.mp3"; fs = fi.file_size or 0
    elif msg.voice:
        fi = msg.voice; fn = f"voice_{int(time.time())}.ogg"; fs = fi.file_size or 0
    else:
        return

    if fs > MAX_SIZE:
        bot.reply_to(msg, f"❌ Файл завеликий: {fmt(fs)}\nЛіміт: {fmt(MAX_SIZE)}")
        return

    pm = bot.reply_to(msg, f"⏳ Зберігаю `{fn}`...")
    try:
        data = bot.download_file(bot.get_file(fi.file_id).file_path)
        res, code = api("POST", f"/upload/{fn}", token=tok(uid), raw=data)
        if code == 200:
            bot.edit_message_text(
                f"✅ *Збережено!*\n\n"
                f"📄 `{fn}`\n"
                f"📦 {fmt(res.get('size',0))} → 🔐 {fmt(res.get('size_enc',0))}\n"
                f"🖥 {res.get('routed_via','?')}",
                msg.chat.id, pm.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 Файли", callback_data="files"),
                    InlineKeyboardButton("↩️ Меню",  callback_data="menu"),
                ))
        else:
            bot.edit_message_text(f"❌ {res.get('message','Помилка')}", msg.chat.id, pm.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ {e}", msg.chat.id, pm.message_id)

# ─── Допоміжні функції ────────────────────────────────────────────────────────
def show_files(uid, cid, mid=None):
    res, code = api("GET", "/files", token=tok(uid))
    if code != 200:
        txt = f"❌ {res.get('message','Помилка')}"
        if mid: bot.edit_message_text(txt, cid, mid)
        else:   bot.send_message(cid, txt)
        return
    files = res.get("files", [])
    quota = res.get("quota", {})
    if not files:
        txt = "📂 *Сховище порожнє*\n\nНадішліть файл у чат!"
        kb  = kb_back()
    else:
        txt = f"📁 *Файли* — {len(files)} шт. | {fmt(quota.get('bytes_encrypted',0))}\n\nОберіть файл:"
        kb  = kb_files(files)
    if mid: bot.edit_message_text(txt, cid, mid, reply_markup=kb)
    else:   bot.send_message(cid, txt, reply_markup=kb)

def send_status(uid, res, cid=None, mid=None):
    st   = res.get("storage_stats", {})
    sats = res.get("satellites", [])
    sat_txt = "\n".join(
        f"{'🟢' if s['status']=='active' else '🔴'} *{s['id']}* — {s.get('requests',0)} запитів"
        for s in sats)
    txt = (
        f"📊 *Стан системи*\n\n"
        f"📤 Завантажень: `{st.get('uploads',0)}`\n"
        f"⬇️ Скачувань: `{st.get('downloads',0)}`\n"
        f"💾 Оброблено: `{fmt(st.get('total_bytes',0))}`\n\n"
        f"*Сервери:*\n{sat_txt}\n\n"
        f"🔒 {res.get('rate_limit','')}\n"
        f"📦 Макс. файл: `{fmt(res.get('max_file_size',0))}`"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔄 Оновити", callback_data="status"),
        InlineKeyboardButton("↩️ Меню",    callback_data="menu"),
    )
    if mid: bot.edit_message_text(txt, cid, mid, reply_markup=kb)
    else:   bot.send_message(uid, txt, reply_markup=kb)

def show_profile(uid, cid, mid=None):
    res, code = api("GET", "/files", token=tok(uid))
    quota = res.get("quota", {}) if code == 200 else {}
    txt = (
        f"👤 *Профіль*\n\n"
        f"Логін: `{uname(uid)}`\n"
        f"📁 Файлів: `{quota.get('files',0)}`\n"
        f"💾 Зайнято: `{fmt(quota.get('bytes_encrypted',0))}`\n"
        f"🔐 Шифрування: `AES-128-CBC`"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📁 Файли", callback_data="files"),
        InlineKeyboardButton("↩️ Меню",  callback_data="menu"),
    )
    if mid: bot.edit_message_text(txt, cid, mid, reply_markup=kb)
    else:   bot.send_message(cid, txt, reply_markup=kb)

# ─── Callback ─────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid  = call.from_user.id
    data = call.data
    cid  = call.message.chat.id
    mid  = call.message.message_id
    bot.answer_callback_query(call.id)

    if data == "menu":
        if logged(uid):
            bot.edit_message_text(f"👤 *{uname(uid)}* | ☁️ KNUBAVaultBot\n\nОберіть дію:", cid, mid, reply_markup=kb_user())
        else:
            bot.edit_message_text("☁️ *KNUBAVaultBot*\n\nОберіть дію:", cid, mid, reply_markup=kb_guest())

    elif data == "cancel":
        clrst(uid)
        if logged(uid): bot.edit_message_text("↩️ Скасовано.", cid, mid, reply_markup=kb_user())
        else:           bot.edit_message_text("↩️ Скасовано.", cid, mid, reply_markup=kb_guest())

    elif data in ("do_login", "guide_login"):
        clrst(uid)
        setst(uid, S_LOGIN_USER)
        bot.edit_message_text(
            "🔓 *Вхід у сховище*\n\nКрок 1 з 2\n👤 Введіть логін:",
            cid, mid, reply_markup=kb_cancel())

    elif data in ("do_register", "guide_register"):
        clrst(uid)
        setst(uid, S_REG_USER)
        bot.edit_message_text(
            "📝 *Реєстрація*\n\nКрок 1 з 2\n👤 Введіть логін:",
            cid, mid, reply_markup=kb_cancel())

    elif data == "files":
        if not tok(uid):
            bot.answer_callback_query(call.id, "🔒 Спочатку увійдіть!", show_alert=True)
            return
        show_files(uid, cid, mid)

    elif data.startswith("file:"):
        fn = data[5:]
        bot.edit_message_text(f"📄 *{fn}*\n\nОберіть дію:", cid, mid, reply_markup=kb_file(fn))

    elif data.startswith("del_ask:"):
        fn = data[8:]
        bot.edit_message_text(
            f"🗑 *Видалити файл?*\n\n`{fn}`\n\n⚠️ Цю дію неможливо скасувати!",
            cid, mid, reply_markup=kb_del_confirm(fn))

    elif data.startswith("del:"):
        fn = data[4:]
        res, code = api("DELETE", f"/delete/{fn}", token=tok(uid))
        if code == 200:
            bot.edit_message_text(f"🗑 Файл `{fn}` видалено.", cid, mid,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 Файли", callback_data="files"),
                    InlineKeyboardButton("↩️ Меню",  callback_data="menu"),
                ))
        else:
            bot.answer_callback_query(call.id, f"❌ {res.get('message','Помилка')}", show_alert=True)

    elif data.startswith("dl:"):
        fn = data[3:]
        bot.send_message(uid, f"⏳ Отримую `{fn}`...")
        raw, code = api("GET", f"/download/{fn}", token=tok(uid))
        if code == 200 and isinstance(raw, bytes):
            bot.send_document(uid, (fn, io.BytesIO(raw), "application/octet-stream"),
                caption=f"📄 `{fn}` | {fmt(len(raw))}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("📁 Файли", callback_data="files")))
        else:
            bot.send_message(uid, f"❌ {raw.get('message','Помилка') if isinstance(raw,dict) else 'Помилка'}")

    elif data == "status":
        res, code = api("GET", "/status")
        if code == 200: send_status(uid, res, cid, mid)
        else: bot.answer_callback_query(call.id, "❌ Недоступний", show_alert=True)

    elif data == "profile":
        if not tok(uid):
            bot.answer_callback_query(call.id, "🔒 Спочатку увійдіть!", show_alert=True)
            return
        show_profile(uid, cid, mid)

    elif data == "webapp":
        bot.edit_message_text(
            f"🌐 *Веб-інтерфейс*\n\nВідкрийте у браузері:\n{SERVER_URL}",
            cid, mid,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🌐 Відкрити", url=SERVER_URL),
                InlineKeyboardButton("↩️ Меню",     callback_data="menu"),
            ))

    elif data == "help":
        bot.edit_message_text(
            "❓ *Довідка*\n\n"
            "/login — увійти\n/register — реєстрація\n"
            "/files — файли\n/profile — профіль\n"
            "/status — стан\n/logout — вийти\n\n"
            "📤 Надішліть файл у чат щоб зберегти.",
            cid, mid, reply_markup=kb_back())

    elif data == "logout":
        clrst(uid)
        if tok(uid):
            api("POST", "/logout", token=tok(uid))
            del sessions[uid]
        bot.edit_message_text(
            "🚪 *Вихід виконано*\n\nДо побачення! Натисніть /start",
            cid, mid, reply_markup=kb_guest())

# ─── Текстові повідомлення + FSM ──────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    uid = msg.from_user.id
    txt = (msg.text or "").strip()
    st  = state(uid)

    # FSM: Вхід крок 1 — логін
    if st == S_LOGIN_USER:
        setst(uid, S_LOGIN_PASS, {"username": txt})
        bot.reply_to(msg,
            f"👤 Логін: `{txt}`\n\nКрок 2 з 2\n🔑 Введіть пароль:",
            reply_markup=kb_cancel())
        return

    # FSM: Вхід крок 2 — пароль
    if st == S_LOGIN_PASS:
        username = sdata(uid).get("username","")
        clrst(uid)
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        res, code = api("POST", "/login", json_data={"username": username, "password": txt})
        if code == 200:
            sessions[uid] = {"token": res["token"], "username": username}
            bot.send_message(uid,
                f"✅ *Вхід виконано!*\n\n👤 *{username}*\n🔐 Сесія активна 24 год\n\nОберіть дію:",
                reply_markup=kb_user())
        else:
            bot.send_message(uid,
                f"❌ *Помилка входу*\n\n{res.get('message','Невірний логін або пароль')}\n\nСпробуйте ще раз:",
                reply_markup=kb_guest())
        return

    # FSM: Реєстрація крок 1 — логін
    if st == S_REG_USER:
        if len(txt) < 3:
            bot.reply_to(msg, "⚠️ Логін мін. 3 символи. Введіть ще раз:", reply_markup=kb_cancel())
            return
        setst(uid, S_REG_PASS, {"username": txt})
        bot.reply_to(msg,
            f"👤 Логін: `{txt}`\n\nКрок 2 з 2\n🔑 Введіть пароль (мін. 4 символи):",
            reply_markup=kb_cancel())
        return

    # FSM: Реєстрація крок 2 — пароль
    if st == S_REG_PASS:
        username = sdata(uid).get("username","")
        clrst(uid)
        if len(txt) < 4:
            bot.send_message(uid, "⚠️ Пароль мін. 4 символи. Спробуйте /register", reply_markup=kb_guest())
            return
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        res, code = api("POST", "/register", json_data={"username": username, "password": txt})
        if code == 201:
            bot.send_message(uid,
                f"✅ *Акаунт створено!*\n\n👤 Логін: `{username}`\n\nТепер увійдіть: /login",
                reply_markup=kb_guest())
        elif code == 409:
            bot.send_message(uid, f"⚠️ Логін `{username}` вже зайнятий.\n\nСпробуйте інший: /register", reply_markup=kb_guest())
        else:
            bot.send_message(uid, f"❌ {res.get('message','Помилка')}")
        return

    # Звичайне повідомлення
    if logged(uid):
        bot.reply_to(msg, "📤 Надішліть файл щоб зберегти.\n\nАбо оберіть дію:", reply_markup=kb_user())
    else:
        bot.reply_to(msg, "🔒 Ви не авторизовані.\n\nНатисніть /start", reply_markup=kb_guest())

# ─── Запуск ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    set_commands()
    print("KNUBAVaultBot запущено!")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
