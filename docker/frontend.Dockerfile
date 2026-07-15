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
RUN chmod +x /docker-entrypoint.d/50-ssl-selfsigned.sh
EXPOSE 80 443
