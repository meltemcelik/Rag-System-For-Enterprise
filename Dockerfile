FROM python:3.11.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
# Ornek korpus (data/docs) VE embedding onbellegi (data/.rag_cache) imaja gomulur.
# Dikkat: Dockerfile satir ICI yorum desteklemez, yorum kendi satirinda olmali.
# Onbellek bilincli olarak DAHIL: compose /app/data/.rag_cache'e bos bir named
# volume bagliyor ve Docker bos volume'u imajdaki icerikle tohumluyor. Onbellek
# imajda olmazsa konteyner ilk acilista 163 parcayi bastan embed etmek zorunda
# kalir. Kullanici DB'si (data/users.db) .dockerignore ile haric tutulur.
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
