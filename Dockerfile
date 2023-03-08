FROM python:3.9-slim

WORKDIR /api

RUN apt-get update && \
    apt-get -y install ffmpeg libavcodec-extra
COPY requirements.txt .
COPY gunicorn.conf.py .

RUN pip install --no-cache-dir --upgrade -r requirements.txt

EXPOSE 80

COPY entrypoint.sh /usr/local/bin
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY ./api/ .

CMD ["entrypoint.sh"]