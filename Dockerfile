FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /srv
RUN apt-get update && apt-get install -y libpq5 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ ./app/
COPY db/ ./db/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY --from=web /web/dist ./web/dist
ENV PORT=8000
CMD ["python", "-m", "app.main"]
