FROM python:3.12-slim

# GDAL/GEOS/PROJ are required by GeoDjango (django.contrib.gis) to read and
# write spatial data and to talk to PostGIS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# `build_colab_bundle` asks git for the commit and the committed code. The
# repository is mounted at /app and owned by the host user, which git refuses
# to read unless the folder is marked safe.
RUN git config --system --add safe.directory /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an ordinary user, not root. uid 1000 is the first user on an Ubuntu
# server, so folders that user creates on the host (data/outbox) stay
# writable from inside. collectstatic writes to staticfiles/, and the seed
# commands download into data/. docker-compose.yml switches back to root for
# development, where the repository is mounted over /app.
RUN useradd --uid 1000 --create-home app     && mkdir -p /app/staticfiles /app/data/outbox     && chown -R app:app /app/staticfiles /app/data
USER app

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
