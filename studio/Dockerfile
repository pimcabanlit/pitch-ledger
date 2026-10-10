# Pitch Ledger Studio (Next.js standalone server). Build context: studio/.
#
# BACKEND_URL is where /api/v1/* is forwarded. Next.js fixes rewrites at build time, so it
# is a build argument; the default is the API container app's in-environment name.
#
#   docker build -t pitch-ledger-studio studio
#   docker build -t pitch-ledger-studio --build-arg BACKEND_URL=http://host.docker.internal:8000 studio

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
ARG BACKEND_URL=http://pitch-ledger-api
ENV BACKEND_URL=$BACKEND_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
