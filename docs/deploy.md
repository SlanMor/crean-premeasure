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

Every night at 2am the scheduler service backs up, off the server, to Hetzner Object Storage (EU):

- **The database**, as a compressed Django dump (it does not depend on the Postgres version). Kept daily for 14 days, then the first of each month for 12 months.
- **The drawings, Callidus files and outputs**, copied file by file, only new or changed ones each night. Working files the platform can make again are left out. A file deleted on the server is removed from the backup 30 days later, so a mistaken deletion can be recovered for a month.

Hetzner's own daily server snapshots carry on as well.

### Setting it up (once)

1. In the Hetzner Cloud console: Object Storage, Create bucket. Location Falkenstein (fsn1): a different data centre from the server in Helsinki, so one incident cannot take both. Name it, for example `crean-premeasure-backup` (names are unique across Hetzner). Keep it **private**.
2. Security, S3 credentials, Generate credentials. Copy the access key and secret key (the secret is shown once).
3. On the server, add to `.env`:

```
BACKUP_S3_ENDPOINT=https://fsn1.your-objectstorage.com
BACKUP_S3_REGION=fsn1
BACKUP_S3_BUCKET=crean-premeasure-backup
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
```

4. `docker compose up -d --build`, then `docker compose exec web python manage.py backup` for a first backup now. Settings, System shows each night's result.

### Restoring (after losing the server)

On a new server, set up as in "First deploy" with the same `.env`, then:

```bash
docker compose up -d --build
docker compose exec web python manage.py restore_backup --list      # the dumps kept
docker compose exec web python manage.py restore_backup             # the latest, and every file
```

`--dump database/2026-11-23-0200.json.gz` restores an earlier one; `--no-files` only the database. Restore into a fresh database (migrations run when the web container starts; do not create a superuser first).

## Alerts

The scheduler checks every 15 minutes and emails the addresses in Settings, System (or every administrator with an email) when: a drawing fails with a program error, a page fails, drawings have waited over 45 minutes or the worker has stopped, the disk is 85% full, or there has been no backup for 26 hours. Each problem is emailed once, again every 12 hours while it lasts, and once more when it clears. Alerts need the email settings in `.env`. "Send a test alert" in Settings, System checks they arrive.

## Clean-up

At 4am the scheduler removes files by the retention settings in Settings, System: working files after 60 days (made again when needed), and, if set, replaced revisions after a number of days and the files of tenders inactive for a number of months. A tender marked "keep this tender's files" never loses its drawings or Callidus files (only working files). `python manage.py cleanup --dry-run` shows what would go.
