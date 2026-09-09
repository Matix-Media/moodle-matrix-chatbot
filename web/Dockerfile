# Nuxt 4 chat frontend — see specs/014-web-chat.md (repo root) for the
# feature this serves, and .env.example (this directory) for its own config.
# pnpm because the official `nuxt module add ui` / `nuxi init --template ui`
# scaffold this project is based on is pnpm-native (pnpm-lock.yaml,
# pnpm-workspace.yaml).
FROM node:22-alpine AS base
WORKDIR /app
RUN corepack enable

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
RUN pnpm run build

FROM base AS runtime
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
RUN addgroup -S nuxt && adduser -S nuxt -G nuxt
COPY --from=build /app/.output ./.output
USER nuxt

EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
