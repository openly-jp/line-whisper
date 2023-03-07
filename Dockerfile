FROM python:3.9-slim

WORKDIR /api

RUN apt-get update && \
    apt-get -y install ffmpeg libavcodec-extra
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt
COPY ./api/ .

EXPOSE 8080

CMD ["gunicorn", "main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8080"]