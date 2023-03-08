#!/bin/sh

sudo docker compose -f docker-compose.prod.yml down
git pull origin
sudo docker compose -f docker-compose.prod.yml up -d --build