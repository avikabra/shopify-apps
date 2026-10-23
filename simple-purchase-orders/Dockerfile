FROM node:24-slim AS base
WORKDIR /app

# Install backend deps (devDeps needed for the build; pruned before runtime)
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Build the embedded frontend
COPY web/package.json web/package-lock.json* ./web/
RUN cd web && npm ci --no-audit --no-fund
COPY web ./web
RUN cd web && npm run build

# Backend sources + prisma
COPY tsconfig.json ./
COPY prisma ./prisma/
COPY src ./src
COPY data ./data
COPY media ./media
RUN npx prisma generate

# Compile backend (prisma generate + tsc -> dist/)
RUN npm run build

# Drop devDependencies for the slimmer runtime image
RUN npm prune --omit=dev --no-audit --no-fund
ENV NODE_ENV=production

EXPOSE 3000
CMD ["sh", "-c", "npx prisma db push --accept-data-loss && node dist/index.js"]
