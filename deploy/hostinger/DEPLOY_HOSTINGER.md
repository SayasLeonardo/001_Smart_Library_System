# Deploying the Smart Library System on a Hostinger VPS

This guide puts the whole system online: the website (librarian / MIS Admin dashboards), the API,
the database and the barcode features. The Raspberry Pi then sends attendance to it over the internet
(see `face_recognition_iot/raspberry_pi/README_RASPBERRY_PI.md`).

```
Raspberry Pi + Camera ──HTTPS + IoT key──►  Hostinger VPS (Nginx → Gunicorn → Flask → database)
                                                      ▲
Librarian's phone / laptop (any network) ──HTTPS──────┘
```

## 0. Why a VPS

Hostinger's documentation says Python and Flask are supported **only on VPS plans**; the regular Web/Cloud
hosting plans cannot run this backend. Choose a Hostinger VPS plan with **at least 2 GB RAM**.
(Menu names in hPanel can change slightly; the steps below describe what to look for.)

## 1. Create the VPS and point a domain at it

1. In hPanel, buy/open your VPS and, when asked for the operating system, choose plain **Ubuntu 24.04**
   (or 22.04). Do not choose a pre-built Django/WordPress/Docker template.
2. Set the server **root password** (or add your SSH key) and note the server's **IP address**.
3. Create a DNS **A record** for the address you want, e.g. `library.yourschool.edu.ph` → the VPS IP
   (hPanel → Domains → DNS / Nameservers, or at wherever the domain is managed).
   Check it from your PC: `nslookup library.yourschool.edu.ph` should show the VPS IP.
   HTTPS cannot be issued until this works. No domain yet? Your VPS overview page shows a hostname
   you may be able to use; confirm it resolves to the IP before continuing.
4. If you enable a firewall in hPanel, allow ports **22, 80 and 443**.

## 2. Get the code onto GitHub and then onto the server

On your Windows PC, in `C:\SmartLibrarySystem001`, commit the changes from this update:

```powershell
git add .
git commit -m "Hostinger VPS deployment + Raspberry Pi kiosk setup"
git push
```

Connect to the server (PowerShell) and clone the project into `/opt/smart-library`:

```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/SayasLeonardo/001_Smart_Library_System.git /opt/smart-library
```

If the repository is private, create a GitHub personal access token (read-only, this repo) and use
`git clone https://YOUR_TOKEN@github.com/SayasLeonardo/001_Smart_Library_System.git /opt/smart-library`,
or upload the folder with WinSCP/`scp` instead.

## 3. Run the setup script

```bash
sudo bash /opt/smart-library/deploy/hostinger/setup_vps.sh library.yourschool.edu.ph you@example.com
```

It installs everything, creates the secrets, starts the app, configures Nginx, opens the firewall,
gets the HTTPS certificate and schedules a daily backup. It takes about 5-10 minutes.
When it finishes, open `https://library.yourschool.edu.ph`.

Show the key the Raspberry Pi will need (keep it private):

```bash
sudo grep SMART_LIBRARY_IOT_KEY /etc/smart-library/smart-library.env
```

## 4. Bring your existing data (keep all records)

Choose the option that matches where your data lives now. **Skip this step** only if you are starting fresh.

### Option A: data is in `smart_library.db` on your PC (local use)

Your database file and uploaded images are in `C:\SmartLibrarySystem001` (`smart_library.db`, `backend\uploads`).

```bash
# on the server
systemctl stop smart-library
```
```powershell
# on your PC, from C:\SmartLibrarySystem001
scp smart_library.db root@YOUR_SERVER_IP:/var/lib/smart-library/
scp -r backend\uploads root@YOUR_SERVER_IP:/var/lib/smart-library/
```
```bash
# on the server
chown -R smartlib:smartlib /var/lib/smart-library
systemctl start smart-library
```

Registered faces are stored inside the database, so recognition works immediately. The app upgrades the
database structure itself at start-up; existing accounts, books, transactions and attendance are kept.

### Option B: data is on Render (PostgreSQL)

Render's free PostgreSQL database is deleted about 6 weeks after creation (see `DEPLOY_NOTES.md`), so do this soon.

```bash
sudo apt-get install -y postgresql postgresql-client
sudo -u postgres psql -c "CREATE USER smartlib WITH PASSWORD 'CHOOSE_A_STRONG_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE smartlib OWNER smartlib;"

# Copy the EXTERNAL database URL from the Render dashboard (library-db > Connections)
pg_dump --no-owner --no-acl "RENDER_EXTERNAL_DATABASE_URL" > /root/render.sql
psql "postgresql://smartlib:CHOOSE_A_STRONG_PASSWORD@127.0.0.1:5432/smartlib" -f /root/render.sql
```

Then edit `/etc/smart-library/smart-library.env`, uncomment `DATABASE_URL=` and set it to
`postgresql://smartlib:CHOOSE_A_STRONG_PASSWORD@127.0.0.1:5432/smartlib`, and run
`systemctl restart smart-library`. Uploaded images are stored inside this database, so they move with it.
(If `pg_dump` reports a version mismatch, install the PostgreSQL client version that matches Render's server.)

## 5. First sign-in and security

1. Sign in as **MIS Admin** and **change the default passwords** (`admin123` and `librarian123`) for both accounts.
   Note: the seeded librarian email is `librarian@paterostechnologicalcollege.edu.ph`
   (the README spells it `labrarian@...`, which is a typo).
2. Add/select people and upload each person's face photo (clear, front-facing, one face, with consent).
3. Never share `/etc/smart-library/smart-library.env`. Change the IoT key if it leaks (edit the file,
   `systemctl restart smart-library`, then update the Pi).

## 6. Everyday operations

| Task | Command (on the server) |
|---|---|
| Is the app running? | `systemctl status smart-library` |
| Live log | `journalctl -u smart-library -f` |
| Restart | `systemctl restart smart-library` |
| Update after a `git push` | `cd /opt/smart-library && git pull && systemctl restart smart-library` |
| Run a backup now | `smart-library-backup` (files go to `/var/backups/smart-library`) |
| Restore SQLite backup | `systemctl stop smart-library; cp /var/backups/smart-library/smart_library_DATE.db /var/lib/smart-library/smart_library.db; chown smartlib:smartlib /var/lib/smart-library/smart_library.db; systemctl start smart-library` |

Backups on the same server do not protect you if the server is lost: download
`/var/backups/smart-library` to another computer regularly (e.g. `scp -r root@SERVER:/var/backups/smart-library .`),
and consider Hostinger's VPS backup/snapshot feature as well.

## 7. Full workflow test (do this after the Pi is set up)

1. From a **phone on mobile data**, open `https://your-domain` and sign in as Librarian. The dashboard loads.
2. Book Transactions → scan a book's barcode with the USB scanner: the book opens; an unknown code says "Book not found".
3. Borrow: scan the borrower's School ID, scan the book barcode, confirm; available copies drop by one.
   Return it; available copies go back up.
4. On the Pi, check the station screen says the camera is ready and it is connected.
5. Upload a face photo for a test person (MIS Admin → People). Within about 30 seconds the Pi can recognize them.
6. Stand in front of the Pi camera. Recognized: choose **Time-in** and confirm
   (or, if auto mode is on, it saves by itself). The librarian's phone (Attendance / Time In-Out page) shows the person inside.
7. Come back later: **Time-out**. The record shows the time-out; "people inside" no longer lists them.
8. Show an unregistered face: nothing is recorded.
9. Unplug the Pi's internet during a scan: the station shows an error and does not save; re-connect and try again.
10. Reboot the Pi and the VPS (`reboot`): everything starts again by itself.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser shows 502 Bad Gateway | App not running: `journalctl -u smart-library -n 50`. Often a missing/invalid `/etc/smart-library/smart-library.env`. |
| Login works but you are sent back to the login page | You are on plain `http://`. Use `https://`; session cookies are HTTPS-only in production. |
| Certbot / HTTPS step failed | The domain's A record does not point to the server yet. Fix DNS, then `certbot --nginx -d your-domain`. |
| Pi shows "returned 401" | The Pi's `SMART_LIBRARY_IOT_KEY` differs from the server's. |
| Pi shows "returned 503 ... key is not configured" | `SMART_LIBRARY_IOT_KEY` is missing in the server env file. |
| Photo/cover upload fails | The limit is 5 MB per image. |
| A person is "pending" and never recognized | Photo has no clear single face. Upload a better photo; check `journalctl -u smart-library` and the Pi log. |

## 9. What was changed in the code

- `backend/app.py`: production settings now work on any host (`SMART_LIBRARY_PRODUCTION`, `SMART_LIBRARY_BEHIND_PROXY`;
  Render still works through `RENDER=true`); the web server also refuses to serve project files such as `*.md`, `*.py`, `deploy/`.
- `face_recognition_iot/app.py`: optional automatic attendance (`SMART_LIBRARY_AUTO_ATTENDANCE=1`, off by default).
- New: `deploy/hostinger/*`, `face_recognition_iot/raspberry_pi/*`, `.gitattributes`.

No `localhost` addresses needed replacing: the web pages call the API with relative paths (`/api/...`), and the
Pi reads the server address from `SMART_LIBRARY_API_URL`.
