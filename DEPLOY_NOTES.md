# Render deployment notes (free plan + Render PostgreSQL)

## Files (same layout as your project)
- render.yaml, requirements.txt            -> project root
- app.js                                   -> same place as your current app.js
- backend/app.py, backend/database.py, backend/face_enrollment.py
- face_recognition_iot/attendance.py
face_recognition_iot/requirements.txt is NOT changed (kiosk PC only).

## How data is stored now
- DATABASE_URL set (Render)  -> PostgreSQL. People, books, attendance AND uploaded images (face photos,
  book covers) are stored in the database, so nothing is lost when the free web service restarts.
- DATABASE_URL not set (your PC) -> SQLite and the backend/uploads folder, exactly as before.

## IMPORTANT: free limits (Render docs)
- Free PostgreSQL expires 30 days after creation, then has a 14-day grace period, then it is DELETED with all data.
  No backups. 1 GB maximum. Only one free database per workspace.
- Free web service sleeps after 15 minutes idle; the first request afterwards takes about a minute.
- To keep data past 30 days you must upgrade the database to a paid instance.

## Deploy
1. Commit and push to GitHub (git add . / git commit / git push).
2. Render dashboard > library-db > Connections: copy the **Internal Database URL**
   (the web service must be in the same region, Oregon).
3. New > Blueprint > pick the repo > Apply. When asked for DATABASE_URL, paste the Internal URL.
4. Open the site URL, log in as MIS Admin, and change the default passwords.
5. Kiosk PC: SMART_LIBRARY_API_URL=https://<service>.onrender.com and SMART_LIBRARY_IOT_KEY=<key from Render>.