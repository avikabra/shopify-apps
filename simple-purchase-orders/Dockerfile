FROM node:24-slim AS base
WORKDIR /app
ENV NODE_ENV=production

# Install backend deps
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --no-audit --no-fund

# Build the embedded frontend
COPY web/package.json web/package-lock.json* ./web/
RUN cd web && npm ci --no-audit --no-fund
COPY web ./web
RUN cd web && npm run build

# Backend sources + prisma
COPY tsconfig.json prisma ./prisma
COPY prisma ./prisma
COPY src ./src
COPY data ./data
RUN npx prisma generate

# Compile backend (prisma generate + tsc -> dist/)
RUN npm run build

EXPOSE 3000
CMD ["sh", "-c", "npx prisma migrate deploy && node dist/index.js"]
