FROM --platform=linux/amd64 node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/ ./
ARG VITE_API_BASE_URL=
RUN npm run build

FROM --platform=linux/amd64 nginx:alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/ssl-entrypoint.sh /docker-entrypoint.d/50-ssl-selfsigned.sh

# Runs as the image's own unprivileged `nginx` user (uid 101), not root (CIS
# Docker Benchmark 4.1). Three things follow from that, and nothing else changes:
#   - it cannot bind ports below 1024, so nginx listens on 8080 and 8443 and the
#     compose files and the Helm chart map the same outside ports onto them;
#   - the pid file moves out of /run, and the directories nginx writes to are
#     handed to it;
#   - the `user` directive only means something to a root master, and is removed
#     so nginx does not warn about it on every start.
# The certificate directory is created here, owned by nginx, so a new volume
# mounted on it is too. A volume from before this change holds a key only root
# can read: docker-compose.yml's `frontend-certs` service puts that right.
RUN chmod +x /docker-entrypoint.d/50-ssl-selfsigned.sh \
 && sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
 && mkdir -p /etc/nginx/certs /var/www/certbot \
 && chown -R nginx:nginx /etc/nginx/certs /etc/nginx/conf.d /var/cache/nginx /var/www/certbot
USER nginx
EXPOSE 8080 8443
