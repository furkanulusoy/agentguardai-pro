# Multi-stage build: compile the real React/Vite production bundle,
# then serve it with a plain nginx (no Node runtime in the final
# image). Built with apps/web itself as the Docker build context (see
# docker-compose.yml) -- this package has no dependency on the rest of
# the monorepo, unlike apps/api.
FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Vite inlines VITE_* variables into the built JS at build time, not
# read at container-start time the way apps/api reads its own .env --
# so this has to be a build ARG, not a runtime environment variable.
# Defaults to what a self-hosted docker-compose stack's apps/api
# container exposes on the host by default (see docker-compose.yml).
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
