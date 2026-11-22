# Deploying to the server

Server: Hetzner CPX32, Helsinki, Ubuntu 24.04, user `deploy`, Docker installed by cloud-init.
Address: crean.markgaffney.com (DNS A record to the server's IPv4).

## First deploy

On your Mac, add the server's deploy key to GitHub so it can pull this private repository:

1. On the server: `ssh-keygen -t ed25519 -C "crean-server" -f ~/.ssh/github -N ""`, then `cat ~/.ssh/github.pub`.
2. On GitHub: repository Settings, Deploy keys, Add deploy key. Paste it, read-only.
3. On the server:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github
EOF
git clone git@github.com:SlanMor/crean-premeasure.git
cd crean-premeasure
cp .env.example .env
```

4. Edit `.env` (`nano .env`): set a secret key and database password with `openssl rand -hex 48` and `openssl rand -hex 24`, and use the same password in `DATABASE_URL`.
5. Start it:

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

6. Check https://crean.markgaffney.com/health/ shows `{"status": "ok"}`. Caddy fetches the HTTPS certificate on first request, once DNS points at the server.

## Updates

```bash
cd ~/crean-premeasure
git pull
docker compose up -d --build
```

Migrations run when the web container starts. After the update that adds the drawing pages, do the one-off set-up in `docs/platform.md`: a user for each estimator, and one background template in the admin.

## Backups

Hetzner takes daily server backups. A nightly database dump to object storage is still to be added.
