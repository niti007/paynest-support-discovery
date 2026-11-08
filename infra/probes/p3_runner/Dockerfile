FROM python:3.12-slim
RUN pip install --no-cache-dir "psycopg[binary]" redis
COPY runner.py /app/runner.py
CMD ["python", "-u", "/app/runner.py"]
