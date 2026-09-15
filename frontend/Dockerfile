# =============================================================================
# Frontend image: build with Vite, serve static assets with nginx.
#
# The build is multi-stage so node_modules and the toolchain never reach the
# runtime image -- nginx serves a handful of static files.
# =============================================================================

FROM node:22-slim AS build

WORKDIR /app

# Dependencies first, so a source-only change does not re-run the install layer.
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# Baked at build time: Vite inlines env vars into the bundle.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# Overrides the default server block: adds the /api proxy and, importantly,
# disables buffering for the SSE chat endpoint.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
