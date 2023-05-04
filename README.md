# LINE-Whisper
LINE Whisper is a simple LINE bot that can easily transcribe speech to text.

## How to start development

- Set the environments in `.env` file based on `.env.template`
- yarn install
- docker-compose -f docker-compose.local.yml up --build

## How to deploy service to EC2

- Change SSH setting. (/etc/ssh/sshd_config)
  - PermitRootLogin no
  - PasswordAuthentication no
  - PermitEmptyPasswords no
- Install Docker (https://docs.docker.com/engine/install/ubuntu/)
- Generate ssh key and register it for Github access.
  - ssh-keygen in ~/.ssh
  - register public key to Github
- Clone this git repository to EC2.
- Set the environments in `.env` file based on `.env.template`.
- Configure nginx based on the following item.
- Configure logrotate based on the following item.
- Start up docker container.
  - `docker compose -f docker-compose.prod.yml up --build -d`
- Configure DNS setting and try it!

### nginx
- install nginx
  ```
  sudo apt install nginx
  sudo systemctl status nginx # check if nginx launched
  ```
- Save SSL certificate and key to the fololwing path.
  - ```/etc/ssl/openly.jp.pem```
  - ```/etc/ssl/openly.jp.key```
  - Notice:
    - If you still set Cloudflare DNS setting as "Proxied", the above certificate should be a ```origin server certificate```.
- Create nginx conf file.
  ```
  sudo vi /etc/nginx/conf.d/line-whisper
  ```
- Write nginx conf to /etc/nginx/sites-enabled/default
  ```
  server {
    listen 80 default_server;
    listen [::]:80 default_server;
    return 301 https://line-api.openly.jp$request_uri;
  }

  server {
    listen 443;
    ssl on;

    ssl_certificate      /etc/ssl/openly.jp.pem;
    ssl_certificate_key  /etc/ssl/openly.jp.key;

    location /line {
          proxy_pass http://localhost:8081/line;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_set_header Host $http_host;
      }

      location /health {
          proxy_pass http://localhost:8081/health;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_set_header Host $http_host;
      }

      location / {
         return 404;
      }
  }
  ```
- Restart nginx.
  ```
  sudo systemctl restart nginx
  ```


### logrotate
- logrotate is a service to delete logs periodically.
- Based on the setting file (logrotate.conf), delete and archive (compress) old logs once per week.
- Run command below to start logrotate.
  ```bash
  sudo chmod 744 logrotate.conf # avoid non-root writable
  sudo logrotate -d logrotate.conf # check if error is caused
  sudo logrotate logrotate.conf
  ```

