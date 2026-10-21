FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ agent/
COPY skills/ skills/
COPY data/ data/
COPY main.py .

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

# Foundry Hosted Agents protocol port (also used as Container Apps target port)
EXPOSE 8088
CMD ["python", "main.py"]
