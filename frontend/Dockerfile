# ─────────────────────────────────────────────────────────────────────────────
# Spring Design Agent – Frontend (Next.js static export via nginx)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Build ─────────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── Stage 2: nginx ─────────────────────────────────────────────────────────
FROM nginx:alpine AS runner
WORKDIR /usr/share/nginx/html

# Copiar el export estático (output: export → genera out/)
COPY --from=builder /app/out ./

# Configuración mínima de nginx: SPA fallback + compresión
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen       3000;
    server_name  localhost;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    location / {
        root   /usr/share/nginx/html;
        index  index.html index.htm;
        try_files $uri $uri/ /index.html;
    }
}
EOF

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
