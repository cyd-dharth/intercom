FROM node:20-slim AS web
WORKDIR /web
# Optional extra CA certs for local networks with TLS-inspecting proxies (e.g. Cloudflare
# Zero Trust gateways). ca-certificates/*.crt is gitignored and empty by default in a
# normal checkout, so this is a no-op in CI or any environment with plain internet access.
COPY ca-certificates/ /usr/local/share/ca-certificates/extra/
RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
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
