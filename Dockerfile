FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run ./run
COPY images ./images
COPY pyproject.toml README.md ./

EXPOSE 8000

# Bind to all interfaces on a fixed port and skip the desktop browser launch -
# there's no display inside the container.
ENV FSATLAS_HOST=0.0.0.0 \
    FSATLAS_PORT=8000 \
    FSATLAS_NO_BROWSER=1

CMD ["python", "-m", "run"]
