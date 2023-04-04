#!/bin/sh

mkdir -p /logs
touch /logs/access.log
touch /logs/error.log

exec gunicorn main:app -k "uvicorn.workers.UvicornWorker" -c "gunicorn.conf.py" --timeout 600