FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .
COPY server.py .
COPY demo.html .
RUN mkdir -p data_storage
EXPOSE 8000
CMD ["python", "bot.py"]
