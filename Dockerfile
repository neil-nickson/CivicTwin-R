FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]"

COPY backend/ backend/
COPY digital_twin/ digital_twin/
COPY experiments/ experiments/
COPY data/ data/
COPY scripts/ scripts/
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
