# Image de l'interface web AVONAM (moteur de trading uniquement — le
# module bank/ n'est pas exposé par web/app.py, voir son en-tête).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY avonam ./avonam
COPY web ./web
COPY data ./data
COPY pyproject.toml .

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
