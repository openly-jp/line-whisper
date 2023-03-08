# LINE-Whisper
LINE Whisper is a simple LINE bot that can easily transcribe speech to text.

## How to Start Development

- Set the environments in `.env` file based on `.env.template`
- yarn install
- docker-compose -f docker-compose.local.yml up --build

## How to deploy service to new server (ubuntu)

- Chanhe SSH setting (/etc/ssh/sshd_config)
  - PermitRootLogin no
  - PasswordAuthentication no
  - PermitEmptyPasswords no
- Install Docker (https://docs.docker.com/engine/install/ubuntu/)
- Generate ssh key and register it for Github access
  - ssh-keygen in ~/.ssh
  - register pub key
- Clone this git repository
- Set `.env` based on `.env.template`
- Start up docker container
  - `docker compose -f docker-compose.prod.yml up --build -d`
