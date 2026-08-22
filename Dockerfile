FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY bring_fast ./bring_fast

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

ENV BRINGFAST_HOST=0.0.0.0 \
    BRINGFAST_PORT=8877 \
    BRINGFAST_DATA=/data

VOLUME ["/data"]
EXPOSE 8877

CMD ["python", "-m", "bring_fast.app"]
