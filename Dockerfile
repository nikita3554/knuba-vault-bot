FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .
COPY server.py .
RUN mkdir -p data_storage
CMD ["python", "bot.py"]
