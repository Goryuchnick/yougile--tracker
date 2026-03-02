FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/
COPY scripts/ scripts/
COPY data/structure.json data/found_priority_sticker.json data/found_alpina_sticker.json data/

RUN useradd --create-home --no-log-init appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "bot/yougile_bot.py"]
