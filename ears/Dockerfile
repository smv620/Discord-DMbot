# ---- build: compile TypeScript, install deps (with build tools for native opus) ----
FROM node:22-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package*.json ./
# npm ci: exactly the package versions in package-lock.json, the ones CI tests (#318). Dev deps are
# needed to compile; they're pruned below.
RUN npm ci --no-audit --no-fund
COPY tsconfig*.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

# ---- runtime: just node, production deps and compiled JS ----
FROM node:22-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json ./
USER node
CMD ["node", "dist/index.js"]
