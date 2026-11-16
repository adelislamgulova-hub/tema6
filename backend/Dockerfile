FROM python:3.12-slim
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY frontend/ /srv/frontend/
ENV PARKCITY_DB=/data/parkcity.sqlite3 PARKCITY_MEDIA=/data/media
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "wsgi:app"]
