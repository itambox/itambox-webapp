FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY itambox/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY itambox/ .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000"]
