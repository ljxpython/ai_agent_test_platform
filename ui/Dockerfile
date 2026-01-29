FROM node:22-bookworm-slim AS build

WORKDIR /app/ui

# Use pnpm via corepack (matches ui/package.json packageManager).
RUN corepack enable && corepack prepare pnpm@10.5.1 --activate

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY ./ ./
RUN pnpm build


FROM node:22-bookworm-slim AS runner

WORKDIR /app/ui
ENV NODE_ENV=production

RUN corepack enable && corepack prepare pnpm@10.5.1 --activate

COPY --from=build /app/ui ./

EXPOSE 3000

# Avoid pnpm argument-forwarding pitfalls ("--" leaking into `next start`).
CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000", "-H", "0.0.0.0"]
