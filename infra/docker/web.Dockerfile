FROM node:20-alpine AS build

WORKDIR /repo

COPY packages/shared /repo/packages/shared
COPY apps/web /repo/apps/web

WORKDIR /repo/apps/web
RUN npm config set registry https://registry.npmmirror.com \
    && npm install --no-audit --no-fund && npm run build

FROM nginx:alpine
COPY infra/docker/web.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80
