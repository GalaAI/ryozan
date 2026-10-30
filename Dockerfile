# BTTCO Unified Build
# Stage 1: Build React frontend
# Stage 2: Python API + built frontend as static files

# ── Stage 1: Frontend ────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /build

# Vite reads VITE_* env vars at build time. Declare as ARG so Railway
# (or any docker build) can inject them via --build-arg, then promote
# to ENV so `npm run build` can see them.
ARG VITE_MAPBOX_TOKEN=""
ARG VITE_STRIPE_PUBLISHABLE_KEY=""
ARG VITE_GOOGLE_CLIENT_ID=""
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN
ENV VITE_STRIPE_PUBLISHABLE_KEY=$VITE_STRIPE_PUBLISHABLE_KEY
ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID

COPY app/package.json app/package-lock.json ./
COPY app/vendor ./vendor
RUN npm ci
COPY app/ ./
RUN npm run build

# ── Stage 2: API + static frontend ───────────────
FROM python:3.12-slim
WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .
COPY --from=frontend /build/dist ./static

RUN mkdir -p uploads

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
