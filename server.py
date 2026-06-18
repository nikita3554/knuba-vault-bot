"""
Локальне хмарне середовище захищеного зберігання даних
Вдосконалена версія: rate limiting, великі файли (chunked), надійне логування
"""

import os
import json
import time
import hashlib
import logging
import secrets
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote
from cryptography.fernet import Fernet

# ─── Налаштування ────────────────────────────────────────────────────────────
STORAGE_DIR     = "./data_storage"
LOG_FILE        = "./cloud_access.log"
USERS_FILE      = "./users.json"
TOKENS_FILE     = "./tokens.json"
SECRET_KEY_FILE = "./secret.key"

MAX_FILE_SIZE   = 500 * 1024 * 1024   # 500 MB — ліміт одного файлу
CHUNK_SIZE      = 4 * 1024 * 1024     # 4 MB — розмір буфера читання
TOKEN_TTL       = 86400               # 24 години
RATE_LIMIT      = 60                  # максимум запитів за хвилину з одного IP
RATE_WINDOW     = 60                  # вікно в секундах

os.makedirs(STORAGE_DIR, exist_ok=True)

# ─── Логування (завжди пише у файл) ──────────────────────────────────────────
logger = logging.getLogger("CloudStorage")
logger.setLevel(logging.INFO)

# Файловий handler — гарантоване запис у cloud_access.log
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-8s] %(message)s",
                                   datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(fh)

# Консольний handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)


def safe_filename(name: str) -> str:
    """Захист від path traversal + URL-decode."""
    name = unquote(name or "")
    name = os.path.basename(name).strip()
    if not name or name in {".", ".."}:
        raise ValueError("Некоректна назва файлу")
    # Заборонені символи у назві файлу
    for ch_ in r'\/:*?"<>|':
        name = name.replace(ch_, "_")
    return name


# ─── Rate Limiter ─────────────────────────────────────────────────────────────
class RateLimiter:
    """Дозволяє не більше RATE_LIMIT запитів за RATE_WINDOW секунд з одного IP."""
    def __init__(self):
        self._lock    = threading.Lock()
        self._buckets = {}   # ip -> [timestamp, ...]

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._buckets.get(ip, [])
            # Залишаємо тільки свіжі записи
            timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
            if len(timestamps) >= RATE_LIMIT:
                self._buckets[ip] = timestamps
                return False
            timestamps.append(now)
            self._buckets[ip] = timestamps
            return True

    def cleanup(self):
        """Очищення застарілих записів (викликати periodically)."""
        now = time.time()
        with self._lock:
            self._buckets = {
                ip: [t for t in ts if now - t < RATE_WINDOW]
                for ip, ts in self._buckets.items()
            }


# ─── Шифрування (Fernet = AES-128-CBC + HMAC-SHA256) ─────────────────────────
class Encryptor:
    def __init__(self):
        if os.path.exists(SECRET_KEY_FILE):
            with open(SECRET_KEY_FILE, "rb") as f:
                self.key = f.read()
        else:
            self.key = Fernet.generate_key()
            with open(SECRET_KEY_FILE, "wb") as f:
                f.write(self.key)
        self.cipher = Fernet(self.key)
        logger.info("Encryptor ініціалізовано (Fernet/AES-128-CBC)")

    def encrypt(self, data: bytes) -> bytes:
        return self.cipher.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self.cipher.decrypt(data)


# ─── Управління користувачами ─────────────────────────────────────────────────
class UserManager:
    def __init__(self):
        self._lock  = threading.Lock()
        self.users  = {}
        self.tokens = {}

        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                self.users = json.load(f)
        else:
            self.register("admin", "admin123", role="admin")

        if os.path.exists(TOKENS_FILE):
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                self.tokens = json.load(f)
            now = time.time()
            self.tokens = {k: v for k, v in self.tokens.items() if v["expires"] > now}

    def _hash(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def register(self, username: str, password: str, role: str = "user") -> bool:
        with self._lock:
            if username in self.users:
                return False
            user_dir = os.path.join(STORAGE_DIR, username)
            os.makedirs(user_dir, exist_ok=True)
            self.users[username] = {
                "password":    self._hash(password),
                "role":        role,
                "created":     datetime.now().isoformat(),
                "storage_dir": user_dir,
            }
            self._save_users()
        logger.info(f"REGISTER | {username} (роль: {role})")
        return True

    def login(self, username: str, password: str) -> str | None:
        user = self.users.get(username)
        if user and user["password"] == self._hash(password):
            token = secrets.token_hex(32)
            with self._lock:
                self.tokens[token] = {
                    "username": username,
                    "expires":  time.time() + TOKEN_TTL,
                }
                self._save_tokens()
            logger.info(f"LOGIN  | {username}")
            return token
        logger.warning(f"LOGIN FAIL | {username} — невірний пароль")
        return None

    def logout(self, token: str) -> bool:
        with self._lock:
            if token in self.tokens:
                username = self.tokens[token]["username"]
                del self.tokens[token]
                self._save_tokens()
                logger.info(f"LOGOUT | {username}")
                return True
        return False

    def validate_token(self, token: str) -> str | None:
        if not token:
            return None
        data = self.tokens.get(token)
        if data and data["expires"] > time.time():
            return data["username"]
        return None

    def _save_users(self):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.users, f, ensure_ascii=False, indent=2)

    def _save_tokens(self):
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.tokens, f, ensure_ascii=False)


# ─── Сховище даних (підтримка великих файлів через chunked read) ──────────────
class DataStorage:
    def __init__(self, encryptor: Encryptor):
        self.enc   = encryptor
        self._lock = threading.Lock()
        self.stats = {"uploads": 0, "downloads": 0, "deletes": 0, "total_bytes": 0}

    def save(self, username: str, filename: str, rfile, content_length: int) -> dict:
        """Читає тіло запиту чанками — підтримка файлів до 500 MB."""
        if content_length > MAX_FILE_SIZE:
            raise ValueError(f"Файл завеликий: {content_length} байт (макс {MAX_FILE_SIZE})")

        user_dir = os.path.join(STORAGE_DIR, username)
        os.makedirs(user_dir, exist_ok=True)

        # Читаємо чанками у пам'ять (для шифрування Fernet потрібен весь блок)
        chunks = []
        remaining = content_length
        while remaining > 0:
            chunk = rfile.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)

        encrypted = self.enc.encrypt(data)
        filepath  = os.path.join(user_dir, filename + ".enc")

        with open(filepath, "wb") as f:
            f.write(encrypted)

        checksum = hashlib.sha256(data).hexdigest()
        md5      = hashlib.md5(data).hexdigest()

        with self._lock:
            self.stats["uploads"]     += 1
            self.stats["total_bytes"] += len(data)

        logger.info(
            f"UPLOAD   | {username} | {filename} | "
            f"{len(data)} байт | SHA256: {checksum[:16]}... | MD5: {md5[:16]}..."
        )
        return {
            "status":   "ok",
            "filename": filename,
            "size":     len(data),
            "size_enc": len(encrypted),
            "checksum": checksum,
            "md5":      md5,
        }

    def load(self, username: str, filename: str) -> bytes | None:
        filepath = os.path.join(STORAGE_DIR, username, filename + ".enc")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            encrypted = f.read()
        data = self.enc.decrypt(encrypted)
        with self._lock:
            self.stats["downloads"] += 1
        logger.info(f"DOWNLOAD | {username} | {filename} | {len(data)} байт")
        return data

    def list_files(self, username: str) -> list:
        user_dir = os.path.join(STORAGE_DIR, username)
        if not os.path.exists(user_dir):
            return []
        files = []
        for fname in os.listdir(user_dir):
            if fname.endswith(".enc"):
                path = os.path.join(user_dir, fname)
                files.append({
                    "name":          fname[:-4],
                    "size_encrypted": os.path.getsize(path),
                    "modified":      datetime.fromtimestamp(
                                         os.path.getmtime(path)
                                     ).strftime("%Y-%m-%d %H:%M:%S"),
                })
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files

    def delete(self, username: str, filename: str) -> bool:
        filepath = os.path.join(STORAGE_DIR, username, filename + ".enc")
        if os.path.exists(filepath):
            os.remove(filepath)
            with self._lock:
                self.stats["deletes"] += 1
            logger.info(f"DELETE   | {username} | {filename}")
            return True
        return False

    def get_user_quota(self, username: str) -> dict:
        user_dir = os.path.join(STORAGE_DIR, username)
        if not os.path.exists(user_dir):
            return {"files": 0, "bytes_encrypted": 0}
        total = sum(
            os.path.getsize(os.path.join(user_dir, f))
            for f in os.listdir(user_dir)
            if f.endswith(".enc")
        )
        count = len([f for f in os.listdir(user_dir) if f.endswith(".enc")])
        return {"files": count, "bytes_encrypted": total}


# ─── Балансер навантаження (Round-Robin) ──────────────────────────────────────
class LoadBalancer:
    def __init__(self):
        self._lock      = threading.Lock()
        self.satellites = [
            {"id": "SAT-1", "port": 8001, "load": 0, "status": "active", "requests": 0},
            {"id": "SAT-2", "port": 8002, "load": 0, "status": "active", "requests": 0},
        ]
        self.request_count = 0

    def get_satellite(self) -> dict:
        with self._lock:
            active = [s for s in self.satellites if s["status"] == "active"]
            if not active:
                return None
            sat = active[self.request_count % len(active)]
            self.request_count += 1
            sat["load"]     += 1
            sat["requests"] += 1
            return sat

    def release(self, sat_id: str):
        with self._lock:
            for s in self.satellites:
                if s["id"] == sat_id:
                    s["load"] = max(0, s["load"] - 1)

    def status(self) -> list:
        return self.satellites


# ─── Глобальні об'єкти ────────────────────────────────────────────────────────
rate_limiter = RateLimiter()
user_mgr     = UserManager()
storage      = DataStorage(Encryptor())
balancer     = LoadBalancer()


# ─── HTTP-обробник ────────────────────────────────────────────────────────────
class CloudHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Вимикаємо стандартний вивід httpserver

    # ── Утиліти ──────────────────────────────────────────────────────────────

    def send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def get_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else None

    def check_rate(self) -> bool:
        ip = self.client_address[0]
        if not rate_limiter.is_allowed(ip):
            logger.warning(f"RATE LIMIT | {ip}")
            self.send_json(429, {"status": "error",
                                 "message": "Забагато запитів. Спробуйте через хвилину."})
            return False
        return True

    def require_auth(self):
        """Повертає username або None (і вже надіслав 401)."""
        token    = self.get_token()
        username = user_mgr.validate_token(token)
        if not username:
            self.send_json(401, {"status": "error", "message": "Не авторизовано"})
        return username

    # ── OPTIONS (preflight) ───────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        if not self.check_rate():
            return
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))

        # /register
        if path == "/register":
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                ok   = user_mgr.register(data["username"], data["password"])
                if ok:
                    self.send_json(201, {"status": "ok",
                                         "message": "Користувача зареєстровано"})
                else:
                    self.send_json(409, {"status": "error",
                                         "message": "Користувач вже існує"})
            except Exception as e:
                self.send_json(400, {"status": "error", "message": str(e)})

        # /login
        elif path == "/login":
            body = self.rfile.read(length)
            try:
                data  = json.loads(body)
                token = user_mgr.login(data["username"], data["password"])
                if token:
                    self.send_json(200, {"status": "ok", "token": token})
                else:
                    self.send_json(401, {"status": "error",
                                          "message": "Невірний логін або пароль"})
            except Exception as e:
                self.send_json(400, {"status": "error", "message": str(e)})

        # /logout
        elif path == "/logout":
            token = self.get_token()
            user_mgr.logout(token)
            self.send_json(200, {"status": "ok", "message": "Вихід виконано"})

        # /upload/<filename>
        elif path.startswith("/upload/"):
            username = self.require_auth()
            if not username:
                return
            try:
                filename = safe_filename(path[8:])
            except ValueError as e:
                self.send_json(400, {"status": "error", "message": str(e)})
                return
            try:
                sat    = balancer.get_satellite()
                result = storage.save(username, filename, self.rfile, length)
                if sat:
                    result["routed_via"] = sat["id"]
                    balancer.release(sat["id"])
                self.send_json(200, result)
            except ValueError as e:
                self.send_json(413, {"status": "error", "message": str(e)})
            except Exception as e:
                logger.error(f"UPLOAD ERROR | {username} | {filename} | {e}")
                self.send_json(500, {"status": "error", "message": str(e)})

        else:
            self.send_json(404, {"status": "error", "message": "Не знайдено"})

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self):
        if not self.check_rate():
            return
        path = urlparse(self.path).path

        # /files
        if path == "/files":
            username = self.require_auth()
            if not username:
                return
            files = storage.list_files(username)
            quota = storage.get_user_quota(username)
            self.send_json(200, {
                "status": "ok",
                "files":  files,
                "count":  len(files),
                "quota":  quota,
            })

        # /download/<filename>
        elif path.startswith("/download/"):
            username = self.require_auth()
            if not username:
                return
            try:
                filename = safe_filename(path[10:])
            except ValueError as e:
                self.send_json(400, {"status": "error", "message": str(e)})
                return
            data = storage.load(username, filename)
            if data is None:
                self.send_json(404, {"status": "error",
                                      "message": "Файл не знайдено"})
                return
            self.send_response(200)
            self.send_header("Content-Type",        "application/octet-stream")
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + quote(filename))
            self.send_header("Content-Length",      str(len(data)))
            self._cors_headers()
            self.end_headers()
            # Передаємо чанками
            offset = 0
            while offset < len(data):
                self.wfile.write(data[offset:offset + CHUNK_SIZE])
                offset += CHUNK_SIZE

        # /status
        elif path == "/status":
            self.send_json(200, {
                "status":        "ok",
                "satellites":    balancer.status(),
                "storage_stats": storage.stats,
                "uptime":        "active",
                "timestamp":     datetime.now().isoformat(),
                "max_file_size": MAX_FILE_SIZE,
                "rate_limit":    f"{RATE_LIMIT} req/{RATE_WINDOW}s",
            })

        # /logs  (тільки для admin)
        elif path == "/logs":
            username = self.require_auth()
            if not username:
                return
            user_info = user_mgr.users.get(username, {})
            if user_info.get("role") != "admin":
                self.send_json(403, {"status": "error",
                                      "message": "Доступ заборонено"})
                return
            if not os.path.exists(LOG_FILE):
                self.send_json(200, {"lines": []})
                return
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-200:]          # останні 200 рядків
            self.send_json(200, {"lines": [l.rstrip() for l in lines]})

        # / або /demo — віддаємо demo.html
        elif path in ("/", "/demo", "/demo.html"):
            demo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.html")
            if not os.path.exists(demo_path):
                self.send_json(404, {"status": "error", "message": "demo.html не знайдено"})
                return
            with open(demo_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)

        else:
            self.send_json(404, {"status": "error", "message": "Не знайдено"})

    # ── DELETE ────────────────────────────────────────────────────────────────
    def do_DELETE(self):
        if not self.check_rate():
            return
        path = urlparse(self.path).path
        if path.startswith("/delete/"):
            username = self.require_auth()
            if not username:
                return
            try:
                filename = safe_filename(path[8:])
            except ValueError as e:
                self.send_json(400, {"status": "error", "message": str(e)})
                return
            ok = storage.delete(username, filename)
            if ok:
                self.send_json(200, {"status": "ok",
                                      "message": f"Файл '{filename}' видалено"})
            else:
                self.send_json(404, {"status": "error",
                                      "message": "Файл не знайдено"})
        else:
            self.send_json(404, {"status": "error", "message": "Не знайдено"})


# ─── Фонове очищення rate-limiter кожні 5 хв ─────────────────────────────────
def _cleanup_loop():
    while True:
        time.sleep(300)
        rate_limiter.cleanup()


# ─── Запуск ───────────────────────────────────────────────────────────────────
def run_server(port: int = None):
    port = port or int(os.environ.get("PORT", 8000))
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", port), CloudHandler)
    logger.info(f"{'='*55}")
    logger.info(f"  Хмарне сховище запущено  →  http://0.0.0.0:{port}")
    logger.info(f"  Адмін: admin / admin123")
    logger.info(f"  Ліміт файлу: {MAX_FILE_SIZE // (1024*1024)} MB")
    logger.info(f"  Rate limit:  {RATE_LIMIT} запитів / {RATE_WINDOW}s")
    logger.info(f"{'='*55}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
