FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads

# Migrate, then serve. A failed migration exits non-zero and the API never starts.
CMD ["sh", "-c", "alembic upgrade head || { echo \"[Ryozan] alembic upgrade head FAILED; refusing to start the API\" >&2; exit 1; }; exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
