# Smart Library System

The existing HTML and CSS are served by Flask, which also provides the SQLite-backed API.

## Step-by-step: run locally on Windows

You need Python installed and a camera connected for face recognition. Run the two applications in **two separate PowerShell terminals**, and keep both terminals open while using the system. Run all commands below from `C:\SmartLibrarySystem001`.

### 1. Set up and start the Smart Library backend

In PowerShell terminal 1:

```powershell
cd C:\SmartLibrarySystem001
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Generate a private key for communication between the backend and camera station:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the generated value for use in both terminals. Do not post it publicly or commit it to the repository. In terminal 1, set the key and start the backend:

```powershell
$env:SMART_LIBRARY_IOT_KEY = "paste-the-generated-key-here"
.\.venv\Scripts\python.exe backend\app.py
```

Leave this terminal running. Open [http://127.0.0.1:5000/](http://127.0.0.1:5000/) to use the main system. The SQLite database is `smart_library.db`; startup preserves existing accounts and records.

### 2. Set up and start Face Recognition / IoT

Open PowerShell terminal 2 and run:

```powershell
cd C:\SmartLibrarySystem001
python -m venv face_recognition_iot\.venv
.\face_recognition_iot\.venv\Scripts\python.exe -m pip install -r face_recognition_iot\requirements.txt
$env:SMART_LIBRARY_IOT_KEY = "paste-the-same-generated-key-here"
$env:SMART_LIBRARY_API_URL = "http://127.0.0.1:5000"
.\face_recognition_iot\.venv\Scripts\python.exe face_recognition_iot\app.py
```

Leave this terminal running too. On its first start, the station downloads the YuNet and SFace model files into the ignored `face_recognition_iot\.models` folder; an internet connection is needed for this download. Open [http://127.0.0.1:5001/](http://127.0.0.1:5001/) and wait for the station status to say the camera is ready.

### 3. Sign in and use face attendance

When created, the default development staff accounts use:

- MIS Admin: `mis@paterostechnologicalcollege.edu.ph` / `admin123`
- Librarian: `labrarian@paterostechnologicalcollege.edu.ph` / `librarian123`

These are not replacement credentials for an existing database; existing accounts and passwords are preserved. Sign in as MIS Admin, add or select a person, and upload that person's face image. Then use the camera station, select **Time-in** or **Time-out**, and confirm the action after the person is recognized. Recognition alone does not record attendance.

### 4. Stop the applications

In each PowerShell terminal, press **Ctrl+C**. Stop the camera station first, then the main backend. Starting them again later requires setting the IoT key in both new terminals again; generate a new key if you no longer have the old one, and use that same new value for both services.

### Troubleshooting

- **Station cannot connect to the library API:** Check that terminal 1 is still running and that `SMART_LIBRARY_API_URL` is `http://127.0.0.1:5000`. Make sure both terminals have the identical `SMART_LIBRARY_IOT_KEY`.
- **Camera is unavailable:** Close other apps using the camera and allow camera access in Windows privacy settings. The station uses camera device `0` by default; to select another device, set `$env:SMART_LIBRARY_CAMERA = "1"` (or the desired device index) in terminal 2 before starting the station.
- **Camera stopped returning frames:** The station now retries the camera connection automatically. Wait a few seconds for it to reconnect; if it does not, close other apps using the camera, verify Windows camera permissions and the `SMART_LIBRARY_CAMERA` device index, then restart the station.
- **Models fail to download:** Check the internet connection, then restart the camera station. The model files are downloaded on startup when missing or incomplete.
- **PowerShell says the address is already in use:** Another copy is likely running. Stop it with **Ctrl+C** in its terminal before starting another copy.

The Flask development servers bind to `127.0.0.1` and are for local development, not production deployment. Change development passwords before any wider deployment; configure a strong `SMART_LIBRARY_SECRET` and HTTPS (`SMART_LIBRARY_HTTPS=1`) in a properly secured deployment.

## Development staff accounts

These accounts are created when missing with the initial passwords below. Existing account passwords are preserved, and people records remain in their current database IDs.

- MIS Admin: `mis@paterostechnologicalcollege.edu.ph` / `admin123`
- Librarian: `labrarian@paterostechnologicalcollege.edu.ph` / `librarian123`

Change these development passwords before exposing the system to a network. In a deployed environment, set a strong `SMART_LIBRARY_SECRET` and use HTTPS (`SMART_LIBRARY_HTTPS=1`).

## Roles and API

MIS Admin and Librarian accounts can use the dashboard, book catalog, borrowing/returns, attendance history, and reports. Only MIS Admins can manage people and system accounts. People records do not receive login passwords.

## Librarian password recovery

Password recovery is handled inside the system and does not use SMTP or external email. From **Forgot Password?**, an active Librarian submits their registered email address. The MIS Admin dashboard polls for recovery requests and provides **View Request**, **Approve**, and **Reject** actions.

The MIS Admin must verify the Librarian in person before approving. After approval, the dashboard displays a six-digit code once for private handoff; the code is not shown in the request list and is valid for 15 minutes. The Librarian enters that code on the verification page and sets a new password of at least eight characters. The code can only be used once. Recovery requests expire after 24 hours if they are not reviewed. Incorrect code submissions are limited, and request creation is rate-limited.

Because this installation has no registered out-of-band channel, code handoff depends on in-person identity verification. Do not approve a request or provide its code based only on the email address in the request. The code is delivered to the authenticated MIS Admin browser only after approval, so it is visible in that approval response; do not copy it into messages, logs, or screenshots.

## Face Recognition / IoT

The main backend stores attendance sessions and face-registration metadata; use `/api/attendance/time-in` and `/api/attendance/time-out` through the camera client. See `face_recognition_iot/README.md` for its independent dependencies, models, and camera configuration. Camera frames and downloaded embeddings stay in memory; the main service stores compact face encodings and the uploaded registration image.

## Book barcodes and borrowing

The Book Transactions screens support USB barcode scanners that act like a keyboard: select **Scan Barcode**, scan the label, and press Enter if the scanner does not send Enter automatically. You can also type or paste barcode values. Add Book has separate **Book Number** and **Barcode** values; use **Generate Barcode** when no physical barcode exists. The generated Code 128 barcode can be previewed and printed from the form.

On first startup after this update, the backend migrates the existing SQLite database in place. Existing book numbers are copied into `books.book_number`, unique barcode values are assigned to those rows, and existing transactions receive barcode and borrower-email snapshots. No manual SQL is required. The new database fields are:

- `books.book_number` — unique catalog number, preserving the legacy value previously held in `books.barcode`.
- `books.barcode` — unique physical/generated barcode used for lookup and borrowing.
- `book_transactions.barcode` and `book_transactions.institutional_email` — values captured when a book is borrowed.

To install the barcode renderer with the other backend dependencies, rerun `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then start the backend normally. Existing book and transaction IDs and relationships are retained.

### Import book catalog from Excel

Sign in as MIS Admin or Librarian, open **Book Transactions**, and choose **UPLOAD EXCEL**. The importer accepts `.xlsx` and `.xls` files up to 5 MB and up to 5,000 book rows. It scans the first 20 rows of each worksheet for a header row and matches columns by name; extra columns are ignored.

Required columns are **Barcode** (or **Book Number / Barcode**), **Book Title**, **Author**, **Category**, and **Book Count** or **Quantity**. **Book Number** and **Date Published** are optional. If no separate book number is supplied, the barcode is also used as the book number. Each imported book starts with all copies available; this imports catalog inventory, not borrower or borrow/return transaction history.

Rows with missing or oversized required values, a non-whole or negative copy count, or a duplicate barcode/book number are skipped and listed in the import summary. Accepted rows are saved in the existing `books` table and appear in the catalog after the upload completes.

To test the feature locally, start the backend using the Windows instructions above, sign in, and prepare a workbook with columns such as `Book Number / Barcode`, `Category`, `Book Title`, `Author Name`, `Date Published`, and `Book Count`. Include one unique valid book, then try uploading the same file again to confirm duplicates are skipped. A row with a blank title or negative copy count should also be reported as skipped.

### Barcode and institutional-email checks

1. Sign in as MIS Admin or Librarian and open **Book Transactions**.
2. Choose **ADD BOOK**, enter the regular book details, then scan into the Barcode field or enter it manually. Use **Generate Barcode** to create a unique Code 128 label; preview it on screen and choose **Print Barcode** to print.
3. Enter a barcode already used by another book and save (or scan it into the field). The form reports that the barcode is already assigned.
4. In the page search box, choose **Scan Barcode**, scan a registered book, and press Enter. The book information opens; an unknown code reports **Book not found**.
5. Choose **Borrow** on a book. Scan or enter the borrower's School ID and press Enter to retrieve the registered name, program, and institutional email. Scan or enter the book barcode, check its availability, then confirm the due date. The transaction stores the email and barcode snapshots and updates available copies.
