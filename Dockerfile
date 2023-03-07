FROM python:3.9-slim

WORKDIR /api

RUN apt-get update && \
    apt-get -y install ffmpeg libavcodec-extra
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt
COPY ./api/ .

EXPOSE 8080
# if you want hot reload, add "--reload",
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]