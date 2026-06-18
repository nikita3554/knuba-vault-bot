FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN echo "v3" && mkdir -p data_storage
EXPOSE 8000
CMD ["python", "bot.py"]
