FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run ./run
COPY images ./images
COPY pyproject.toml README.md ./

EXPOSE 8000

# Bind to all interfaces on a fixed port and skip the desktop browser launch -
# there's no display inside the container. FSATLAS_DATA_DIR points settings.json/
# saved_items.json at a dedicated directory (instead of the default run/ next to the app
# code) so it can be bind-mounted from the host without shadowing anything - see
# docker-compose.yml.
ENV FSATLAS_HOST=0.0.0.0 \
    FSATLAS_PORT=8000 \
    FSATLAS_NO_BROWSER=1 \
    FSATLAS_DATA_DIR=/data

CMD ["python", "-m", "run"]
