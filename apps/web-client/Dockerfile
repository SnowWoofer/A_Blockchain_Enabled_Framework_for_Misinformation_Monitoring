# --- build the SPA ----------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Exact versions from the lockfile; install scripts are not needed by any dependency.
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build

# --- serve it behind the TLS edge -----------------------------------------
# Unprivileged nginx: runs as a plain uid (the host user, so it can read the
# 0600 TLS key) with no capabilities at all.
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.conf nginx/proxy.conf /etc/nginx/snippets/
COPY --chmod=755 nginx/40-require-config.sh /docker-entrypoint.d/40-require-config.sh
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
ENV GATEWAY_UPSTREAM=blockchain-gateway:8000 \
    FACT_CHECK_UPSTREAM=fact-checking-service:8000 \
    INGEST_UPSTREAM=claim-ingest-worker:8000 \
    PUBLIC_HTTPS_PORT=8443 \
    HSTS_MAX_AGE=0 \
    DNS_RESOLVER=127.0.0.11
EXPOSE 8080 8443
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/ || exit 1
