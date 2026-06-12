FROM python:3.12-slim

WORKDIR /app

# Встановлюємо залежності
RUN pip install --no-cache-dir cryptography

# Копіюємо файли
COPY server.py .
COPY demo.html .

# Створюємо директорії
RUN mkdir -p data_storage

# Відкриваємо порт
EXPOSE 8000

# Запускаємо сервер
CMD ["python", "server.py"]
