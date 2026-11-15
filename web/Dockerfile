# Сборка фронта + раздача статики (§18.3).
# Многостадийная: node собирает dist, nginx раздаёт и проксирует /api на бэкенд.
FROM node:20-alpine AS build

WORKDIR /app

# Слой зависимостей отдельно от кода — кеш npm между сборками.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Режим API зашивается в бандл на сборке: mock — работает без бэкенда,
# sse — живой webapp.py (переключает Юсеф при готовности эндпоинтов).
ARG VITE_API_MODE=mock
ENV VITE_API_MODE=$VITE_API_MODE
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
