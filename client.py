"""
Клієнт для тестування хмарного сховища (оновлена версія)
Демонструє: реєстрацію, вхід, upload, download, список файлів, видалення,
            тест rate limiting, тест великих файлів, logout
"""

import urllib.request
import urllib.error
import json
import time
import os

BASE_URL = "http://localhost:8000"


def req(method, path, data=None, token=None, binary=False):
    url = BASE_URL + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None
    if data is not None:
        if isinstance(data, bytes):
            body = data
            headers["Content-Type"] = "application/octet-stream"
        else:
            body = json.dumps(data).encode()

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            if binary:
                return resp.read(), resp.status
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {"error": f"HTTP {e.code}"}, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def measure_speed(size_kb, token, label):
    data = os.urandom(size_kb * 1024)
    fname = f"test_{size_kb}kb.bin"

    t0 = time.time()
    req("POST", f"/upload/{fname}", data=data, token=token)
    up_time = time.time() - t0

    t0 = time.time()
    req("GET", f"/download/{fname}", token=token, binary=True)
    down_time = time.time() - t0

    up_speed   = (size_kb / 1024) / up_time   if up_time   > 0 else 0
    down_speed = (size_kb / 1024) / down_time if down_time > 0 else 0

    req("DELETE", f"/delete/{fname}", token=token)
    print(f"  {label:15s} | Upload: {up_speed:6.2f} MB/s ({up_time*1000:6.1f} ms)"
          f" | Download: {down_speed:6.2f} MB/s ({down_time*1000:6.1f} ms)")
    return up_time, down_time, up_speed, down_speed


def main():
    print("=" * 65)
    print("  ТЕСТУВАННЯ ЛОКАЛЬНОГО ХМАРНОГО СХОВИЩА ДАНИХ (v2)")
    print("=" * 65)

    # 1. Реєстрація
    print("\n[1] Реєстрація нового користувача...")
    res, code = req("POST", "/register", {"username": "testuser", "password": "pass123"})
    print(f"    Статус {code}: {res.get('message', res)}")

    # 2. Авторизація
    print("\n[2] Авторизація...")
    res, code = req("POST", "/login", {"username": "testuser", "password": "pass123"})
    token = res.get("token")
    print(f"    Статус {code}: токен отримано ({token[:20]}...)" if token else f"    Помилка: {res}")
    if not token:
        print("Авторизація не вдалася. Завершення.")
        return

    # 3. Завантаження файлів
    print("\n[3] Завантаження файлів у сховище...")
    files = {
        "document.txt": b"Confidential document content - Hello World!",
        "config.json":  b'{"server": "cloud", "version": "2.0", "secure": true}',
        "report.csv":   b"date,size,status\n2026-06-01,1024,ok\n2026-06-02,2048,ok",
    }
    for fname, content in files.items():
        res, code = req("POST", f"/upload/{fname}", data=content, token=token)
        print(f"    {fname}: {res.get('size','?')} байт | {res.get('status')} | "
              f"через {res.get('routed_via','?')} | MD5: {res.get('md5','')[:12]}...")

    # 4. Список файлів
    print("\n[4] Список файлів у сховищі...")
    res, code = req("GET", "/files", token=token)
    for f in res.get("files", []):
        print(f"    {f['name']:30s} | {f['size_encrypted']} байт (enc) | {f.get('modified','')}")
    quota = res.get("quota", {})
    print(f"    Квота: {quota.get('files',0)} файлів, {quota.get('bytes_encrypted',0)} байт на диску")

    # 5. Завантаження файлу
    print("\n[5] Отримання файлу зі сховища...")
    data, code = req("GET", "/download/document.txt", token=token, binary=True)
    if isinstance(data, bytes):
        print(f"    document.txt: {data.decode()}")

    # 6. Тест невірного пароля
    print("\n[6] Тест захисту — невірний пароль...")
    res, code = req("POST", "/login", {"username": "testuser", "password": "WRONG"})
    print(f"    Статус {code}: {res.get('message')}")

    # 7. Тест без токена
    print("\n[7] Тест захисту — запит без токена...")
    res, code = req("GET", "/files")
    print(f"    Статус {code}: {res.get('message')}")

    # 8. Тест rate limiting (10 швидких запитів)
    print("\n[8] Тест rate limiting (10 запитів підряд)...")
    blocked = 0
    for i in range(10):
        res, code = req("GET", "/status")
        if code == 429:
            blocked += 1
    print(f"    Заблоковано: {blocked}/10 (rate limit порог: 60 req/хв)")

    # 9. Вимірювання швидкості
    print("\n[9] Вимірювання швидкості передачі даних...")
    print(f"  {'Розмір':15s} | {'Upload':30s} | {'Download'}")
    print("  " + "-" * 70)
    results = []
    for size_kb in [10, 100, 500, 1024]:
        r = measure_speed(size_kb, token, f"{size_kb} KB")
        results.append((size_kb, *r))

    # 10. Стан системи
    print("\n[10] Стан системи...")
    res, code = req("GET", "/status")
    print(f"     Timestamp:    {res.get('timestamp')}")
    print(f"     Rate limit:   {res.get('rate_limit')}")
    print(f"     Max file:     {res.get('max_file_size', 0) // (1024*1024)} MB")
    print(f"     Uploads:      {res['storage_stats']['uploads']} файлів")
    for sat in res.get("satellites", []):
        print(f"     {sat['id']}: статус={sat['status']}, запитів={sat.get('requests', 0)}")

    # 11. Видалення
    print("\n[11] Видалення файлу...")
    res, code = req("DELETE", "/delete/document.txt", token=token)
    print(f"     Статус {code}: {res.get('message')}")

    # 12. Logout
    print("\n[12] Logout (інвалідація токена)...")
    res, code = req("POST", "/logout", token=token)
    print(f"     Статус {code}: {res.get('message')}")
    res, code = req("GET", "/files", token=token)
    print(f"     Перевірка старого токена: статус {code} — {res.get('message')}")

    print("\n" + "=" * 65)
    print("  ТЕСТУВАННЯ ЗАВЕРШЕНО УСПІШНО")
    print("=" * 65)
    return results


if __name__ == "__main__":
    main()
