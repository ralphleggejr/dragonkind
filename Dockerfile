FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dragonbot ./dragonbot
COPY route.json .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "dragonbot", "run"]
