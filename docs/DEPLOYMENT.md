# Deploying FloodScout

How the live system is put together and how to set it up again. The files it
uses are `docker-compose.prod.yml`, `deploy/Caddyfile` and
`.env.production.example`.

```
             visitors
                |
           Cloudflare (DNS, HTTPS, CDN; Free plan)
          /                         \
 floodscout.me                       api.floodscout.me
 Cloudflare Pages                    Azure VM, Ubuntu 24.04 (B2ats v2)
 (the Leaflet dashboard)               Caddy :443 -> gunicorn (Django API)
                                       scheduler (daily refresh, alerts)
                                            |
                              Azure Database for PostgreSQL 16
                              Flexible Server B1ms + PostGIS
```

- **HTTPS twice.** Cloudflare ends the visitor's HTTPS. It then connects to
  Caddy over HTTPS again (SSL mode **Full (strict)**), and Caddy presents a
  Cloudflare origin certificate kept in `deploy/certs/` on the server only.
- **Only Cloudflare gets in.** Caddy drops connections that do not come from
  Cloudflare's address ranges, and takes the visitor's address from
  `CF-Connecting-IP`. The rate limits use that address (`DJANGO_NUM_PROXIES=1`).
- **Database.** The database is Azure's own and is reached over TLS
  (`DB_SSLMODE=require`). Only the VM's address is allowed through its firewall.
- **Containers.** The containers run as an ordinary user (uid 1000), not root.
  `web` runs the migrations and `collectstatic` each time it starts.

## 1. Azure (portal)

1. Use Azure for Students and create everything from **Free services**. Add a
   USD 5 budget alert.
2. Create the resource group `floodscout-rg`, in one region for everything.
3. Create the database: PostgreSQL Flexible Server `floodscout-db`, version 16,
   Burstable B1ms, 32 GiB, admin `floodscout_admin`. In **Server parameters >
   `azure.extensions`**, tick **POSTGIS**. Add a database named `floodscout`.
4. Create the VM `floodscout-vm`: Ubuntu Server 24.04 x64, B2ats v2, SSH key
   for `azureuser`, static public IP.
5. Add a database firewall rule for the VM's public IP.
6. VM inbound rules: SSH 22 from your own address only, and 443. Port 80 is
   not needed.

## 2. Cloudflare

- Point the domain's nameservers at Cloudflare and use the Free plan.
- DNS: add an `A` record for `api` pointing at the VM's IP, **Proxied**.
- SSL/TLS: set the mode to **Full (strict)**. Create an origin certificate and
  save the certificate and key as two files. Cloudflare shows the key only once.

## 3. The server

```bash
# Docker, and 2 GB of swap (the VM has 1 GiB of memory)
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker azureuser
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# log out and in again; then clone with a read-only GitHub deploy key
git clone git@github.com:KelvinMurega/FloodScout.git floodscout && cd floodscout
mkdir -p deploy/certs data/outbox
# copy the origin certificate to deploy/certs/origin.pem and the key to
# deploy/certs/origin.key (scp from your PC), then: chmod 600 deploy/certs/origin.key
cp .env.production.example .env && nano .env   # fill in every <...>
```

Turn on PostGIS once:

```bash
docker run --rm -it postgis/postgis:16-3.4 psql \
  "host=floodscout-db.postgres.database.azure.com dbname=floodscout user=floodscout_admin sslmode=require" \
  -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

Build and start. The first build takes 10 to 20 minutes, because of GDAL:

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f web
```

## 4. Data

Either restore a `pg_dump -Fc` of the local database with `pg_restore
--no-owner --no-privileges` into the Azure database, or seed from scratch:
`seed_regions` (both levels), `seed_terrain`, `seed_rainfall`, `score_regions`,
`flood_outlook` (both levels), `seed_flood_events`, then `createsuperuser`. Do
not seed the area grid on the same day, because it uses up the free Open-Meteo
allowance.

## 5. The dashboard (Cloudflare Pages)

Connect the repository with the Vite preset. Set the root to `frontend`, the
build command to `npm ci && npm run build` and the output to `dist`. Set the
environment variables `VITE_API_BASE_URL=https://api.floodscout.me/api` and
`NODE_VERSION=22`, then add the custom domain `floodscout.me`.

## 6. Checks

| Check | Expect |
|---|---|
| `https://api.floodscout.me/api/health/` | `{"status": "ok", ...}` |
| `https://floodscout.me` on a phone on mobile data | the map with today's scores |
| `curl -k https://<VM IP>/api/health/ -H "Host: api.floodscout.me"` from outside Cloudflare | connection closed |
| `docker compose -f docker-compose.prod.yml logs scheduler` | rainfall fetched, regions scored |
| 11 wrong passwords at sign-in | the 11th is refused with "too many requests" |

For Google sign-in, add `https://floodscout.me` as an authorised JavaScript
origin and `https://api.floodscout.me/api/auth/social/google/callback/` as a
redirect URI.

## Updating

```bash
cd ~/floodscout && git pull
docker compose -f docker-compose.prod.yml up -d --build
```

The dashboard rebuilds by itself when its branch changes. Azure keeps 7 days of
database backups.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| `web` restarts, log says "SSL required" or "no pg_hba.conf entry" | `DB_SSLMODE` is not `require`, or the VM is not in the database firewall |
| `type "geometry" does not exist` | PostGIS is not enabled |
| Cloudflare error 526 | The origin certificate is missing or wrong in `deploy/certs/` |
| Cloudflare error 521 or 522 | Caddy is not running, or 443 is closed on the VM |
| Every visitor's connection is closed | Cloudflare changed its ranges; update the list in `deploy/Caddyfile` |
| Too many redirects | `DJANGO_SSL_REDIRECT=True`, or the Cloudflare SSL mode is not Full (strict) |
