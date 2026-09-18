FROM node:22-slim
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
COPY public ./public
RUN mkdir -p /app/data
ENV PORT=3000 DATA_DIR=/app/data
EXPOSE 3000
CMD ["node", "--import", "tsx", "src/server.ts"]
