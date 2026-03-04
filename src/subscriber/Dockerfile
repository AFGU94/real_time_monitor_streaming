FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

ENV PORT=8080
EXPOSE 8080
# sync worker evita el bug "Invalid file descriptor -1" en gthread al cerrar la conexión tras 204
# sh -c para expandir $PORT; exec para que gunicorn reciba señales
CMD ["sh", "-c", "exec gunicorn --bind :${PORT:-8080} --workers 1 --worker-class sync --timeout 60 main:app"]
