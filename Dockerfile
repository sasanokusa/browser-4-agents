FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && playwright install --with-deps firefox

EXPOSE 8765
ENTRYPOINT ["browsr"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0"]
