# Render deployment notes

## Where each file goes (matches your folder)

```
01Smart_Final_Updated_System/
├── render.yaml                      NEW   (project root)
├── requirements.txt                 NEW   (project root, for Render)
├── app.js                           REPLACE (same place as your current app.js)
├── backend/
│   ├── app.py                       REPLACE
│   ├── database.py                  REPLACE
│   └── face_enrollment.py           NEW
└── face_recognition_iot/
    └── attendance.py                REPLACE
```
Nothing else changes. face_recognition_iot/requirements.txt stays as it is (kiosk PC only).

## Before you push to GitHub
Make sure the root .gitignore contains (so data and venvs are not uploaded):
```
.venv/
__pycache__/
backend/uploads/
smart_library.db*
face_recognition_iot/.venv/
```
Optional: copy face_recognition_iot/.models/*.onnx into backend/models/ and commit them. The server then
does not depend on downloading the models during the build.

## Deploy
1. Push to GitHub. Render: New > Blueprint > select the repo (reads render.yaml).
2. Render generates SMART_LIBRARY_SECRET and SMART_LIBRARY_IOT_KEY. Copy the IOT key from the Environment tab.
3. Kiosk PC (camera station keeps running locally):
   SMART_LIBRARY_API_URL=https://<your-service>.onrender.com
   SMART_LIBRARY_IOT_KEY=<same key>
4. Change the default admin/librarian passwords after first login.
5. To keep existing data: copy smart_library.db to /var/data and backend/uploads/* to /var/data/uploads via Render SSH.
