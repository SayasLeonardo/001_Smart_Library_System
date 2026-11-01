"""Flask API and static-file server for the Smart Library System."""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import io
import mimetypes
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from database import (
        DATA_DIR, DATABASE_PATH, DatabaseError, IntegrityError, USING_POSTGRES,
        get_connection, initialize_database,
    )
    import face_enrollment
except ModuleNotFoundError:  # pragma: no cover - supports running from repo root
    from backend.database import (
        DATA_DIR, DATABASE_PATH, DatabaseError, IntegrityError, USING_POSTGRES,
        get_connection, initialize_database,
    )
    from backend import face_enrollment


PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Uploads live on the Render persistent disk when SMART_LIBRARY_DATA_DIR is set,
# otherwise in the original backend/uploads folder.
UPLOAD_ROOT = (DATA_DIR / "uploads") if DATA_DIR else (Path(__file__).resolve().parent / "uploads")
COVER_DIRECTORY = UPLOAD_ROOT / "book_covers"
FACE_DIRECTORY = UPLOAD_ROOT / "person_faces"
IS_RENDER = os.environ.get("RENDER") == "true"
# With PostgreSQL, uploaded images are stored in the database (Render's free web service has
# no permanent disk). Without it, they stay on disk exactly as before.
STORE_FILES_IN_DATABASE = USING_POSTGRES
MAX_COVER_SIZE = 5 * 1024 * 1024
MANILA_TIME = ZoneInfo("Asia/Manila")
app = Flask(__name__)
_secret_key = os.environ.get("SMART_LIBRARY_SECRET", "")
if not _secret_key:
    if IS_RENDER:
        raise RuntimeError("SMART_LIBRARY_SECRET must be set when running on Render.")
    _secret_key = "local-development-key-change-before-deployment"
app.config.update(
    SECRET_KEY=_secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SMART_LIBRARY_HTTPS", "1" if IS_RENDER else "0") == "1",
    MAX_CONTENT_LENGTH=MAX_COVER_SIZE + 64 * 1024,
)
if IS_RENDER:
    # Render terminates HTTPS at its proxy; trust one hop so Flask sees https and the real client.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
initialize_database()


def api_error(message, status=400):
    return jsonify({"error": message}), status


# --- Uploaded image storage -------------------------------------------------------------
# kind is "cover" (book covers) or "face" (people face images).
def _store_directory(kind):
    return COVER_DIRECTORY if kind == "cover" else FACE_DIRECTORY


def store_file(kind, filename, data):
    if STORE_FILES_IN_DATABASE:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO stored_files (kind, name, content_type, data) VALUES (?, ?, ?, ?)
                   ON CONFLICT (kind, name) DO UPDATE SET data = excluded.data,
                   content_type = excluded.content_type""",
                (kind, filename, content_type, data),
            )
    else:
        write_file_durably(_store_directory(kind) / filename, data)


def read_stored_file(kind, filename):
    """Return (bytes, content_type) or None when the file does not exist."""
    if Path(filename).name != filename:
        return None
    if STORE_FILES_IN_DATABASE:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT data, content_type FROM stored_files WHERE kind = ? AND name = ?", (kind, filename)
            ).fetchone()
        if not row:
            return None
        return bytes(row["data"]), row["content_type"]
    path = _store_directory(kind) / filename
    if not path.is_file():
        return None
    return path.read_bytes(), mimetypes.guess_type(filename)[0] or "application/octet-stream"


def delete_stored_file(kind, filename):
    if not filename or Path(filename).name != filename:
        return
    if STORE_FILES_IN_DATABASE:
        with get_connection() as connection:
            connection.execute("DELETE FROM stored_files WHERE kind = ? AND name = ?", (kind, filename))
    else:
        (_store_directory(kind) / filename).unlink(missing_ok=True)


def stored_file_response(kind, filename):
    if STORE_FILES_IN_DATABASE:
        found = read_stored_file(kind, filename)
        if not found:
            abort(404)
        response = Response(found[0], mimetype=found[1])
        response.headers["Cache-Control"] = "no-cache"
        return response
    return send_from_directory(_store_directory(kind), filename, max_age=0)



def signed_in_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, school_id, name, email, role, department, status "
            "FROM users WHERE id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
    return dict(user) if user else None


@app.before_request
def load_signed_in_user():
    g.user = signed_in_user()


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not g.user:
            return api_error("Please log in to continue.", 401)
        return handler(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorate(handler):
        @wraps(handler)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                return api_error("You do not have permission to perform this action.", 403)
            return handler(*args, **kwargs)

        return wrapped

    return decorate


def iot_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        expected_key = os.environ.get("SMART_LIBRARY_IOT_KEY", "")
        supplied_key = request.headers.get("X-IOT-API-Key", "")
        if not expected_key:
            return api_error("IoT API key is not configured on the main backend.", 503)
        if not supplied_key or not hmac.compare_digest(supplied_key, expected_key):
            return api_error("A valid IoT API key is required.", 401)
        return handler(*args, **kwargs)

    return wrapped


def clean_text(data, field, maximum=200):
    value = str(data.get(field, "")).strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{field.replace('_', ' ').capitalize()} is required and must be under {maximum} characters.")
    return value


def recovery_secret_hash(value):
    secret = app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def recovery_audit(connection, request_id, action, actor_id=None):
    connection.execute(
        "INSERT INTO password_recovery_audit (request_id, actor_id, action, created_at) VALUES (?, ?, ?, ?)",
        (request_id, actor_id, action, manila_now()),
    )


def consume_recovery_rate_limit(connection, action, key, limit, window_seconds):
    now = datetime.now(MANILA_TIME)
    window_start = datetime.fromtimestamp(
        int(now.timestamp()) // window_seconds * window_seconds,
        MANILA_TIME,
    ).isoformat(timespec="seconds")
    key_hash = recovery_secret_hash(key)
    connection.execute(
        """INSERT INTO password_recovery_rate_limits (action, key_hash, window_start, attempts)
           VALUES (?, ?, ?, 1)
           ON CONFLICT(action, key_hash, window_start)
           DO UPDATE SET attempts = attempts + 1""",
        (action, key_hash, window_start),
    )
    count = connection.execute(
        """SELECT attempts FROM password_recovery_rate_limits
           WHERE action = ? AND key_hash = ? AND window_start = ?""",
        (action, key_hash, window_start),
    ).fetchone()["attempts"]
    connection.execute(
        "DELETE FROM password_recovery_rate_limits WHERE window_start < ?",
        ((now - timedelta(days=1)).isoformat(timespec="seconds"),),
    )
    return count <= limit


def transaction_rows(search="", date_from="", date_to=""):
    try:
        if date_from:
            date.fromisoformat(date_from)
        if date_to:
            date.fromisoformat(date_to)
    except ValueError:
        raise ValueError("Dates must use the YYYY-MM-DD format.")
    if date_from and date_to and date_from > date_to:
        raise ValueError("The start date must not be after the end date.")

    sql = """
        SELECT t.id, t.book_id, t.borrower_id, b.book_number, t.barcode, b.title,
               COALESCE(NULLIF(t.borrower_school_id, ''), u.school_id, '') AS borrower_school_id,
               COALESCE(NULLIF(t.borrower_name, ''), u.name, '') AS borrower_name,
               COALESCE(NULLIF(t.borrower_department, ''), u.department, '') AS borrower_department,
               t.institutional_email AS borrower_email,
               COALESCE(NULLIF(t.processor_name, ''), staff.name, '') AS processed_by,
               t.borrow_date, t.due_date, t.return_date,
               t.status,
            CASE WHEN t.status = 'borrowed' AND t.due_date < ?
                    THEN 'overdue' ELSE t.status END AS display_status
        FROM book_transactions t
        JOIN books b ON b.id = t.book_id
        LEFT JOIN users u ON u.id = t.borrower_id
        LEFT JOIN users staff ON staff.id = t.processed_by
        WHERE (b.title LIKE ? OR b.book_number LIKE ? OR b.barcode LIKE ?
               OR COALESCE(NULLIF(t.borrower_name, ''), u.name, '') LIKE ?
               OR COALESCE(NULLIF(t.borrower_school_id, ''), u.school_id, '') LIKE ?)
    """
    search_term = f"%{search}%"
    parameters = [manila_now()[:10], search_term, search_term, search_term, search_term, search_term]
    if date_from and date_to:
        sql += " AND ((date(t.borrow_date) BETWEEN ? AND ?) OR (date(t.return_date) BETWEEN ? AND ?))"
        parameters.extend([date_from, date_to, date_from, date_to])
    elif date_from:
        sql += " AND (date(t.borrow_date) >= ? OR date(t.return_date) >= ?)"
        parameters.extend([date_from, date_from])
    elif date_to:
        sql += " AND (date(t.borrow_date) <= ? OR date(t.return_date) <= ?)"
        parameters.extend([date_to, date_to])
    sql += " ORDER BY t.created_at DESC, t.id DESC"
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def manila_now():
    return datetime.now(MANILA_TIME).isoformat(timespec="seconds")


def attendance_rows(search="", date_from="", date_to="", role="", department="", event_type="all"):
    for value in (date_from, date_to):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("Dates must use the YYYY-MM-DD format.") from error
    if date_from and date_to and date_from > date_to:
        raise ValueError("The start date must not be after the end date.")
    if event_type not in ("all", "time_in", "time_out"):
        raise ValueError("Choose all attendance, Time-in, or Time-out records.")

    sql = """
        SELECT a.id, u.id AS person_id,
               COALESCE(NULLIF(a.person_school_id, ''), u.school_id, '') AS school_id,
               COALESCE(NULLIF(a.person_name, ''), u.name, '') AS name,
               COALESCE(NULLIF(a.person_role, ''), NULLIF(u.person_type, ''), u.role, '') AS role,
               COALESCE(NULLIF(a.person_department, ''), u.department, '') AS department,
               a.attendance_date, a.time_in, a.time_out, a.source,
               CASE a.status WHEN 'admin_closed' THEN 'Closed by Admin'
                    WHEN 'inside' THEN 'Inside' ELSE 'Completed' END AS status
        FROM attendance_records a
        LEFT JOIN users u ON u.id = a.person_id
        WHERE (COALESCE(a.person_school_id, u.school_id, '') LIKE ?
               OR COALESCE(a.person_name, u.name, '') LIKE ?)
          AND (? = '' OR COALESCE(NULLIF(a.person_role, ''), NULLIF(u.person_type, ''), u.role, '') = ?)
          AND (? = '' OR COALESCE(a.person_department, u.department, '') LIKE ?)
          AND (u.id IS NULL OR u.role NOT IN ('MIS Admin', 'Librarian'))
    """
    search_term = f"%{search}%"
    parameters = [search_term, search_term, role, role, f"%{department}%" if department else "", f"%{department}%" if department else ""]
    if event_type == "time_in":
        sql += " AND a.time_in IS NOT NULL"
    elif event_type == "time_out":
        sql += " AND a.time_out IS NOT NULL"
    if date_from and date_to:
        if event_type == "time_in":
            sql += " AND substr(a.time_in, 1, 10) BETWEEN ? AND ?"
            parameters.extend([date_from, date_to])
        elif event_type == "time_out":
            sql += " AND substr(a.time_out, 1, 10) BETWEEN ? AND ?"
            parameters.extend([date_from, date_to])
        else:
            sql += " AND (substr(a.time_in, 1, 10) BETWEEN ? AND ? OR substr(a.time_out, 1, 10) BETWEEN ? AND ?)"
            parameters.extend([date_from, date_to, date_from, date_to])
    elif date_from:
        if event_type == "all":
            sql += " AND (substr(a.time_in, 1, 10) >= ? OR (a.time_out IS NOT NULL AND substr(a.time_out, 1, 10) >= ?))"
            parameters.extend([date_from, date_from])
        else:
            field = "a.time_out" if event_type == "time_out" else "a.time_in"
            sql += f" AND substr({field}, 1, 10) >= ?"
            parameters.append(date_from)
    elif date_to:
        if event_type == "all":
            sql += " AND (substr(a.time_in, 1, 10) <= ? OR (a.time_out IS NOT NULL AND substr(a.time_out, 1, 10) <= ?))"
            parameters.extend([date_to, date_to])
        else:
            field = "a.time_out" if event_type == "time_out" else "a.time_in"
            sql += f" AND substr({field}, 1, 10) <= ?"
            parameters.append(date_to)
    sql += " ORDER BY a.time_in DESC, a.id DESC"
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, school_id, name, email, password_hash, role, department "
            "FROM users WHERE lower(email) = ? AND status = 'active'",
            (email,),
        ).fetchone()
    if (not user or user["role"] not in ("MIS Admin", "Librarian") or not user["password_hash"]
            or not check_password_hash(user["password_hash"], password)):
        return api_error("Email or password is incorrect.", 401)
    session.clear()
    session["user_id"] = user["id"]
    with get_connection() as connection:
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (manila_now(), user["id"]))
    return jsonify({"user": {key: user[key] for key in ("id", "school_id", "name", "email", "role", "department")}})


@app.get("/api/auth/me")
@login_required
def get_session_user():
    return jsonify({"user": g.user})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "You have been logged out."})


@app.post("/api/auth/change-password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    password = str(data.get("newPassword", ""))
    confirmation = str(data.get("confirmPassword", ""))
    if len(password) < 8:
        return api_error("Password must be at least 8 characters long.")
    if password != confirmation:
        return api_error("The passwords do not match.")
    with get_connection() as connection:
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), g.user["id"]))
    session.clear()
    return jsonify({"message": "Password updated. Please log in again."})


@app.post("/api/auth/recovery/request")
def request_recovery():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    if not email or len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return api_error("Enter a valid institutional email address.")
    access_key = secrets.token_urlsafe(32)
    response_message = "If eligible, your recovery request is submitted for MIS Admin review."
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not consume_recovery_rate_limit(
            connection, "request", request.remote_addr or "unknown", 5, 3600
        ):
            return api_error("Too many recovery requests. Please try again later.", 429)
        user = connection.execute(
            "SELECT id, email, role, status FROM users WHERE lower(email) = ? AND role = 'Librarian' AND status = 'active'",
            (email,),
        ).fetchone()
        if not user:
            return jsonify({
                "message": response_message,
                "request_key": access_key,
            })
        active = connection.execute(
            """SELECT id, status, expires_at FROM password_recovery_requests
               WHERE user_id = ? AND status IN ('pending', 'approved')
               ORDER BY requested_at DESC LIMIT 1""",
            (user["id"],),
        ).fetchone()
        now = datetime.now(MANILA_TIME)
        if active and datetime.fromisoformat(active["expires_at"]) <= now:
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'expired', reviewed_at = ?, code_hash = '', reset_token_hash = NULL
                   WHERE id = ?""",
                (manila_now(), active["id"]),
            )
            recovery_audit(connection, active["id"], "expired")
            active = None
        if active:
            access_key = secrets.token_urlsafe(32)
            connection.execute(
                "UPDATE password_recovery_requests SET token_hash = ? WHERE id = ?",
                (recovery_secret_hash(access_key), active["id"]),
            )
            recovery_audit(connection, active["id"], "request_revisited")
            return jsonify({
                "message": response_message,
                "request_key": access_key,
            })
        expires_at = (now + timedelta(hours=24)).isoformat(timespec="seconds")
        cursor = connection.execute(
            """INSERT INTO password_recovery_requests
               (user_id, email, code_hash, token_hash, status, requested_at, expires_at)
               VALUES (?, ?, '', ?, 'pending', ?, ?)""",
            (user["id"], user["email"], recovery_secret_hash(access_key), manila_now(), expires_at),
        )
        recovery_audit(connection, cursor.lastrowid, "requested")
        return jsonify({
            "message": response_message,
            "request_key": access_key,
        })


@app.post("/api/auth/recovery/status")
def get_recovery_status():
    data = request.get_json(silent=True) or {}
    request_key = str(data.get("request_key", "")).strip()
    if not request_key or len(request_key) > 200:
        return api_error("Recovery request is unavailable.", 404)
    with get_connection() as connection:
        row = connection.execute(
            """SELECT pr.id, pr.status, pr.expires_at, pr.verified_at,
                      pr.verify_attempts, u.status AS user_status, u.role
               FROM password_recovery_requests pr
               JOIN users u ON u.id = pr.user_id
               WHERE pr.token_hash = ?""",
            (recovery_secret_hash(request_key),),
        ).fetchone()
        if not row or row["role"] != "Librarian" or row["user_status"] != "active":
            return jsonify({"status": "pending", "expires_at": None, "verified": False})
        status = row["status"]
        if status in ("pending", "approved") and datetime.fromisoformat(row["expires_at"]) <= datetime.now(MANILA_TIME):
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'expired', reviewed_at = ?, code_hash = '', reset_token_hash = NULL
                   WHERE id = ? AND status IN ('pending', 'approved')""",
                (manila_now(), row["id"]),
            )
            recovery_audit(connection, row["id"], "expired")
            status = "expired"
        return jsonify({
            "status": "verified" if row["verified_at"] and status == "approved" else status,
            "expires_at": row["expires_at"] if status == "approved" else None,
            "verified": bool(row["verified_at"]),
        })


@app.get("/api/auth/recovery/requests")
@roles_required("MIS Admin")
def list_recovery_requests():
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT pr.id, pr.user_id, COALESCE(u.name, 'Deleted Librarian') AS librarian_name,
                      pr.email, pr.status,
                      pr.requested_at, pr.expires_at, pr.approved_by, approver.name AS approved_by_name,
                      pr.approved_at, pr.reviewed_at, pr.used_at
               FROM password_recovery_requests pr
               LEFT JOIN users u ON u.id = pr.user_id
               LEFT JOIN users approver ON approver.id = pr.approved_by
               WHERE u.role = 'Librarian' OR pr.user_id IS NULL
               ORDER BY CASE pr.status WHEN 'pending' THEN 0 ELSE 1 END, pr.requested_at DESC
               LIMIT 100""",
        ).fetchall()
        for row in rows:
            if row["status"] in ("pending", "approved") and datetime.fromisoformat(row["expires_at"]) <= datetime.now(MANILA_TIME):
                connection.execute(
                    """UPDATE password_recovery_requests
                       SET status = 'expired', reviewed_at = ?, code_hash = '', reset_token_hash = NULL
                       WHERE id = ? AND status IN ('pending', 'approved')""",
                    (manila_now(), row["id"]),
                )
                recovery_audit(connection, row["id"], "expired")
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT pr.id, pr.user_id, COALESCE(u.name, 'Deleted Librarian') AS librarian_name,
                      pr.email, pr.status,
                      pr.requested_at, pr.expires_at, pr.approved_by, approver.name AS approved_by_name,
                      pr.approved_at, pr.reviewed_at, pr.used_at
               FROM password_recovery_requests pr
               LEFT JOIN users u ON u.id = pr.user_id
               LEFT JOIN users approver ON approver.id = pr.approved_by
               WHERE u.role = 'Librarian' OR pr.user_id IS NULL
               ORDER BY CASE pr.status WHEN 'pending' THEN 0 ELSE 1 END, pr.requested_at DESC
               LIMIT 100"""
        ).fetchall()
    return jsonify({"requests": [dict(row) for row in rows]})


@app.delete("/api/auth/recovery/requests/<int:request_id>")
@roles_required("MIS Admin")
def delete_recovery_request(request_id):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        recovery_request = connection.execute(
            "SELECT status FROM password_recovery_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not recovery_request:
            return api_error("Password recovery request not found.", 404)
        if recovery_request["status"] not in ("completed", "expired"):
            return api_error("Only completed or expired recovery requests can be deleted.", 409)
        recovery_audit(connection, request_id, f"deleted_request:{request_id}", g.user["id"])
        connection.execute(
            "DELETE FROM password_recovery_requests WHERE id = ?",
            (request_id,),
        )
    return jsonify({"message": "Password recovery request deleted."})


@app.post("/api/auth/recovery/approve")
@roles_required("MIS Admin")
def approve_recovery_request():
    data = request.get_json(silent=True) or {}
    request_id = data.get("request_id")
    decision = str(data.get("decision", "approve")).strip().lower()
    if not isinstance(request_id, int) or request_id < 1:
        return api_error("Recovery request ID is required.")
    if decision not in ("approve", "reject"):
        return api_error("Decision must be approve or reject.")
    recovery_code = f"{secrets.randbelow(1000000):06d}" if decision == "approve" else None
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not consume_recovery_rate_limit(
            connection, "review", str(g.user["id"]), 30, 3600
        ):
            return api_error("Too many recovery decisions. Please try again later.", 429)
        request_row = connection.execute(
            """SELECT pr.id, pr.user_id, pr.email, pr.status, pr.expires_at, u.name AS librarian_name,
                      u.status AS user_status
               FROM password_recovery_requests pr
               JOIN users u ON u.id = pr.user_id
               WHERE pr.id = ? AND u.role = 'Librarian'""",
            (request_id,),
        ).fetchone()
        if not request_row:
            return api_error("Recovery request not found.", 404)
        if request_row["status"] != "pending":
            return api_error("This recovery request has already been processed.", 409)
        if request_row["user_status"] != "active" or datetime.fromisoformat(request_row["expires_at"]) <= datetime.now(MANILA_TIME):
            connection.execute(
                """UPDATE password_recovery_requests SET status = 'expired', approved_by = ?,
                   reviewed_at = ?, code_hash = '', reset_token_hash = NULL WHERE id = ?""",
                (g.user["id"], manila_now(), request_row["id"]),
            )
            recovery_audit(connection, request_row["id"], "expired", g.user["id"])
            return api_error("This recovery request has expired or the account is inactive.", 410)
        timestamp = manila_now()
        if decision == "approve":
            expires_at = (datetime.now(MANILA_TIME) + timedelta(minutes=15)).isoformat(timespec="seconds")
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'approved', code_hash = ?, reset_token_hash = NULL, verify_attempts = 0,
                       verified_at = NULL, approved_by = ?, approved_at = ?, reviewed_at = ?, expires_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (
                recovery_secret_hash(recovery_code),
                    g.user["id"], timestamp, timestamp, expires_at, request_row["id"],
                ),
            )
            recovery_audit(connection, request_row["id"], "approved", g.user["id"])
            return jsonify({
                "message": "Approved. Verify the Librarian in person and privately provide this one-time code.",
                "request_id": request_row["id"],
                "recovery_code": recovery_code,
                "expires_at": expires_at,
            })
        connection.execute(
            """UPDATE password_recovery_requests
               SET status = 'rejected', code_hash = '', reset_token_hash = NULL,
                   approved_by = ?, approved_at = ?, reviewed_at = ?
               WHERE id = ? AND status = 'pending'""",
            (g.user["id"], timestamp, timestamp, request_row["id"]),
        )
        recovery_audit(connection, request_row["id"], "rejected", g.user["id"])
        return jsonify({"message": "Recovery request rejected.", "request_id": request_row["id"]})


@app.post("/api/auth/recovery/verify")
def verify_recovery_code():
    data = request.get_json(silent=True) or {}
    request_key = str(data.get("request_key", "")).strip()
    code = str(data.get("code", "")).strip()
    if not request_key or not re.fullmatch(r"\d{6}", code):
        return api_error("Enter the six-digit code provided by the MIS Admin.")
    reset_token = secrets.token_urlsafe(32)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not consume_recovery_rate_limit(
            connection, "verify", request_key, 5, 900
        ):
            return api_error("Too many verification attempts. Start a new recovery request later.", 429)
        request_row = connection.execute(
            """SELECT pr.id, pr.user_id, pr.code_hash, pr.status, pr.expires_at,
                      pr.verified_at, pr.verify_attempts, u.role, u.status AS user_status
               FROM password_recovery_requests pr
               JOIN users u ON u.id = pr.user_id
               WHERE pr.token_hash = ?""",
            (recovery_secret_hash(request_key),),
        ).fetchone()
        if not request_row or request_row["role"] != "Librarian" or request_row["user_status"] != "active":
            return api_error("Recovery request is unavailable.", 404)
        if request_row["status"] != "approved":
            return api_error("This recovery request is not approved or is no longer active.", 409)
        if request_row["verified_at"]:
            return api_error("This verification code has already been used.", 409)
        if datetime.fromisoformat(request_row["expires_at"]) <= datetime.now(MANILA_TIME):
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'expired', reviewed_at = ?, code_hash = '', reset_token_hash = NULL
                   WHERE id = ?""",
                (manila_now(), request_row["id"]),
            )
            recovery_audit(connection, request_row["id"], "expired")
            return api_error("This recovery code has expired. Start a new request for MIS Admin approval.", 410)
        expected_hash = recovery_secret_hash(code)
        if not hmac.compare_digest(request_row["code_hash"], expected_hash):
            attempts = request_row["verify_attempts"] + 1
            if attempts >= 5:
                connection.execute(
                    """UPDATE password_recovery_requests
                       SET status = 'expired', verify_attempts = ?, reviewed_at = ?, code_hash = ''
                       WHERE id = ? AND status = 'approved'""",
                    (attempts, manila_now(), request_row["id"]),
                )
                recovery_audit(connection, request_row["id"], "verification_locked")
            else:
                connection.execute(
                    "UPDATE password_recovery_requests SET verify_attempts = ? WHERE id = ? AND status = 'approved'",
                    (attempts, request_row["id"]),
                )
                recovery_audit(connection, request_row["id"], "verification_failed")
            return api_error("The recovery code is invalid.", 401)
        timestamp = manila_now()
        connection.execute(
            """UPDATE password_recovery_requests
               SET verified_at = ?, reset_token_hash = ?, code_hash = ''
               WHERE id = ? AND status = 'approved' AND verified_at IS NULL""",
            (timestamp, recovery_secret_hash(reset_token), request_row["id"]),
        )
        recovery_audit(connection, request_row["id"], "verified")
        return jsonify({
            "message": "Code verified. You may now set a new password.",
            "reset_token": reset_token,
        })


@app.post("/api/auth/recovery/reset")
def reset_password_from_recovery():
    data = request.get_json(silent=True) or {}
    request_key = str(data.get("request_key", "")).strip()
    reset_token = str(data.get("reset_token", "")).strip()
    password = str(data.get("newPassword", ""))
    confirmation = str(data.get("confirmPassword", ""))
    if not request_key or not reset_token or not password or not confirmation:
        return api_error("The verified recovery session and both password fields are required.")
    if len(password) < 8:
        return api_error("Password must be at least 8 characters long.")
    if password != confirmation:
        return api_error("The passwords do not match.")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        request_row = connection.execute(
            """SELECT pr.id, pr.user_id, pr.status, pr.expires_at, pr.verified_at,
                      pr.reset_token_hash, u.role, u.status AS user_status
               FROM password_recovery_requests pr
               JOIN users u ON u.id = pr.user_id
               WHERE pr.token_hash = ?""",
            (recovery_secret_hash(request_key),),
        ).fetchone()
        if (not request_row or request_row["role"] != "Librarian"
                or request_row["user_status"] != "active" or request_row["status"] != "approved"
                or not request_row["verified_at"] or not request_row["reset_token_hash"]
                or not hmac.compare_digest(
                    request_row["reset_token_hash"], recovery_secret_hash(reset_token)
                )):
            return api_error("A verified, active recovery session is required.", 401)
        if datetime.fromisoformat(request_row["expires_at"]) <= datetime.now(MANILA_TIME):
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'expired', reviewed_at = ?, reset_token_hash = NULL
                   WHERE id = ?""",
                (manila_now(), request_row["id"]),
            )
            recovery_audit(connection, request_row["id"], "expired")
            return api_error("This recovery request has expired.", 410)
        user_update = connection.execute(
            """UPDATE users SET password_hash = ?
               WHERE id = ? AND role = 'Librarian' AND status = 'active'""",
            (generate_password_hash(password), request_row["user_id"]),
        )
        if user_update.rowcount != 1:
            return api_error("The Librarian account is no longer active.", 409)
        connection.execute(
            """UPDATE password_recovery_requests
               SET status = 'completed', used_at = ?, reviewed_at = ?, reset_token_hash = NULL,
                   code_hash = ''
               WHERE id = ? AND status = 'approved' AND verified_at IS NOT NULL""",
            (manila_now(), manila_now(), request_row["id"]),
        )
        recovery_audit(connection, request_row["id"], "completed", request_row["user_id"])
    return jsonify({"message": "Password reset complete. Please log in with your new password."})


@app.get("/api/books")
@login_required
def list_books():
    search = f"%{request.args.get('q', '').strip()}%"
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, book_number, barcode, title, author, category, published_date, cover_image,
                      quantity, available_quantity,
                      CASE WHEN available_quantity > 0 THEN 'Available' ELSE 'Not Available' END AS status
               FROM books WHERE status = 'active'
                 AND (book_number LIKE ? OR barcode LIKE ? OR title LIKE ? OR author LIKE ? OR category LIKE ?)
               ORDER BY lower(title)""",
            (search, search, search, search, search),
        ).fetchall()
    return jsonify({"books": [dict(row) for row in rows]})


@app.get("/api/books/barcode")
@login_required
def find_book_by_barcode():
    barcode = request.args.get("value", "").strip()
    if not barcode:
        return api_error("Enter or scan a barcode.")
    with get_connection() as connection:
        book = connection.execute(
            """SELECT id, book_number, barcode, title, author, category, published_date, cover_image,
                      quantity, available_quantity,
                      CASE WHEN available_quantity > 0 THEN 'Available' ELSE 'Not Available' END AS status
               FROM books WHERE barcode = ? AND status = 'active'""",
            (barcode,),
        ).fetchone()
    if not book:
        return api_error("Book not found.", 404)
    return jsonify({"book": dict(book)})


@app.get("/api/books/barcode-image")
@login_required
def render_book_barcode():
    barcode_value = request.args.get("value", "").strip()
    if not barcode_value or len(barcode_value) > 50:
        return api_error("Enter a valid barcode.")
    from barcode import Code128
    from barcode.writer import SVGWriter

    image = Code128(barcode_value, writer=SVGWriter()).render(
        writer_options={
            "module_width": 0.28,
            "module_height": 12,
            "quiet_zone": 2,
            "font_size": 9,
            "text_distance": 2,
        }
    )
    return Response(image, content_type="image/svg+xml")


@app.post("/api/books/generate-barcode")
@roles_required("MIS Admin", "Librarian")
def generate_book_barcode():
    with get_connection() as connection:
        for _ in range(10):
            barcode_value = "SL" + secrets.token_hex(6).upper()
            exists = connection.execute(
                "SELECT 1 FROM books WHERE barcode = ?", (barcode_value,)
            ).fetchone()
            if not exists:
                return jsonify({"barcode": barcode_value})
    return api_error("Unable to generate a unique barcode. Please try again.", 503)


def cover_extension(image_data):
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return ".webp"
    return None


@app.get("/api/books/<int:book_id>/cover")
@login_required
def get_book_cover(book_id):
    with get_connection() as connection:
        book = connection.execute(
            "SELECT cover_image FROM books WHERE id = ? AND status = 'active'", (book_id,)
        ).fetchone()
    if not book or not book["cover_image"]:
        abort(404)
    return stored_file_response("cover", book["cover_image"])


@app.post("/api/books/<int:book_id>/cover")
@roles_required("MIS Admin", "Librarian")
def upload_book_cover(book_id):
    upload = request.files.get("cover")
    if not upload:
        return api_error("Choose an image file to upload.")
    image_data = upload.stream.read(MAX_COVER_SIZE + 1)
    if len(image_data) > MAX_COVER_SIZE:
        return api_error("Book images must be 5 MB or smaller.", 413)
    extension = cover_extension(image_data)
    if not extension:
        return api_error("Choose a valid PNG, JPEG, GIF, or WebP image.")

    with get_connection() as connection:
        book = connection.execute(
            "SELECT cover_image FROM books WHERE id = ? AND status = 'active'", (book_id,)
        ).fetchone()
    if not book:
        return api_error("Book not found.", 404)

    filename = secrets.token_hex(16) + extension
    store_file("cover", filename, image_data)
    try:
        with get_connection() as connection:
            connection.execute("UPDATE books SET cover_image = ? WHERE id = ?", (filename, book_id))
    except Exception:
        delete_stored_file("cover", filename)
        raise

    delete_stored_file("cover", book["cover_image"])
    return jsonify({"message": "Book image updated.", "cover_image": filename})


@app.delete("/api/books/<int:book_id>/cover")
@roles_required("MIS Admin", "Librarian")
def reset_book_cover(book_id):
    with get_connection() as connection:
        book = connection.execute(
            "SELECT cover_image FROM books WHERE id = ? AND status = 'active'", (book_id,)
        ).fetchone()
        if not book:
            return api_error("Book not found.", 404)
        connection.execute("UPDATE books SET cover_image = '' WHERE id = ?", (book_id,))
    delete_stored_file("cover", book["cover_image"])
    return jsonify({"message": "Book image reset to the default."})


@app.post("/api/books")
@roles_required("MIS Admin", "Librarian")
def create_book():
    data = request.get_json(silent=True) or {}
    try:
        book_number = clean_text(data, "book_number", 50)
        barcode = clean_text(data, "barcode", 50)
        title = clean_text(data, "title")
        author = clean_text(data, "author")
        category = clean_text(data, "category")
        published_date = str(data.get("published_date", "")).strip()
        quantity = int(data.get("quantity", 0))
        if quantity < 0:
            raise ValueError("Book count cannot be negative.")
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO books
                   (book_number, barcode, title, author, category, published_date, quantity, available_quantity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_number, barcode, title, author, category, published_date, quantity, quantity),
            )
        return jsonify({"id": cursor.lastrowid, "message": "Book added."}), 201
    except IntegrityError:
        with get_connection() as connection:
            if connection.execute("SELECT 1 FROM books WHERE barcode = ?", (data.get("barcode"),)).fetchone():
                return api_error("This barcode is already assigned to another book.", 409)
        return api_error("That book number is already in use.", 409)


@app.patch("/api/books/<int:book_id>")
@roles_required("MIS Admin", "Librarian")
def update_book(book_id):
    data = request.get_json(silent=True) or {}
    allowed_fields = {"book_number", "barcode", "title", "author", "category", "published_date", "quantity"}
    values = {key: data[key] for key in allowed_fields if key in data}
    if not values:
        return api_error("No book details were provided.")
    try:
        for field in ("book_number", "barcode", "title", "author", "category"):
            if field in values:
                values[field] = clean_text(values, field, 50 if field in ("book_number", "barcode") else 200)
        if "quantity" in values:
            new_quantity = int(values["quantity"])
            with get_connection() as connection:
                current = connection.execute("SELECT quantity, available_quantity FROM books WHERE id = ?", (book_id,)).fetchone()
                if not current:
                    return api_error("Book not found.", 404)
                borrowed = current["quantity"] - current["available_quantity"]
                if new_quantity < borrowed:
                    return api_error("Book count cannot be lower than the number of borrowed copies.")
                values["quantity"] = new_quantity
                values["available_quantity"] = new_quantity - borrowed
        fields = ", ".join(f"{field} = ?" for field in values)
        with get_connection() as connection:
            cursor = connection.execute(f"UPDATE books SET {fields} WHERE id = ?", (*values.values(), book_id))
            if cursor.rowcount == 0:
                return api_error("Book not found.", 404)
        return jsonify({"message": "Book updated."})
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        if "barcode" in values:
            with get_connection() as connection:
                if connection.execute("SELECT 1 FROM books WHERE barcode = ? AND id != ?",
                                      (values["barcode"], book_id)).fetchone():
                    return api_error("This barcode is already assigned to another book.", 409)
        return api_error("That book number is already in use.", 409)


@app.delete("/api/books/<int:book_id>")
@roles_required("MIS Admin", "Librarian")
def delete_book(book_id):
    with get_connection() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM book_transactions WHERE book_id = ? AND status = 'borrowed'", (book_id,)
        ).fetchone()[0]
        if active:
            return api_error("Return all borrowed copies before removing this book.", 409)
        cursor = connection.execute("UPDATE books SET status = 'archived' WHERE id = ? AND status = 'active'", (book_id,))
    if not cursor.rowcount:
        return api_error("Book not found.", 404)
    return jsonify({"message": "Book removed."})


@app.get("/api/borrowers")
@login_required
def find_borrowers():
    term = request.args.get("q", "").strip()
    with get_connection() as connection:
        if request.args.get("exact") == "1":
            # Exact School ID match (used by the Borrow Book auto-fill)
            rows = connection.execute(
                """SELECT id, school_id, name, email, role, department FROM users
                   WHERE status = 'active' AND role IN ('Student', 'Faculty')
                   AND LOWER(school_id) = LOWER(?)
                   ORDER BY lower(name) LIMIT 20""",
                (term,),
            ).fetchall()
        else:
            search = f"%{term}%"
            rows = connection.execute(
                """SELECT id, school_id, name, email, role, department FROM users
                   WHERE status = 'active' AND role IN ('Student', 'Faculty')
                   AND (school_id LIKE ? OR name LIKE ? OR email LIKE ?)
                   ORDER BY lower(name) LIMIT 20""",
                (search, search, search),
            ).fetchall()
    return jsonify({"borrowers": [dict(row) for row in rows]})


@app.get("/api/transactions")
@roles_required("MIS Admin", "Librarian")
def list_transactions():
    try:
        rows = transaction_rows(
            request.args.get("q", "").strip(), request.args.get("from", ""), request.args.get("to", "")
        )
        return jsonify({"transactions": rows})
    except ValueError as error:
        return api_error(str(error))


@app.post("/api/transactions/borrow")
@roles_required("MIS Admin", "Librarian")
def borrow_book():
    data = request.get_json(silent=True) or {}
    try:
        book_id = int(data.get("book_id", 0))
        school_id = clean_text(data, "borrower_school_id", 50)
        barcode = str(data.get("barcode", "")).strip()
        institutional_email = str(data.get("institutional_email", "")).strip()
        due_date = str(data.get("due_date") or (datetime.now(MANILA_TIME).date() + timedelta(days=14)).isoformat())
        date.fromisoformat(due_date)
    except (ValueError, TypeError):
        return api_error("Choose a valid book, borrower school ID, and due date.")
    if not institutional_email:
        return api_error("Enter the borrower's institutional email address.")
    if len(institutional_email) > 254 or not re.fullmatch(
        r"[A-Za-z0-9._%+-]+@paterostechnologicalcollege\.edu\.ph", institutional_email, re.IGNORECASE
    ):
        return api_error("The institutional email must end with @paterostechnologicalcollege.edu.ph.")
    institutional_email = institutional_email.lower()

    try:
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if barcode:
                book = connection.execute(
                    "SELECT id, barcode, available_quantity FROM books WHERE barcode = ? AND status = 'active'",
                    (barcode,),
                ).fetchone()
                if book_id and book and book["id"] != book_id:
                    return api_error("The scanned barcode does not match the selected book.", 409)
            else:
                book = connection.execute(
                    "SELECT id, barcode, available_quantity FROM books WHERE id = ? AND status = 'active'",
                    (book_id,),
                ).fetchone()
            borrower = connection.execute(
                """SELECT id, school_id, name, email, department FROM users
                   WHERE school_id = ? AND status = 'active' AND role IN ('Student', 'Faculty')""",
                (school_id,),
            ).fetchone()
            if not book:
                return api_error("Book not found.", 404)
            if not borrower:
                return api_error("No active student or faculty member has that school ID.", 404)
            if book["available_quantity"] < 1:
                return api_error("There are no available copies of this book.", 409)
            connection.execute(
                """INSERT INTO book_transactions
                   (book_id, borrower_id, processed_by, borrower_school_id, borrower_name,
                    borrower_department, processor_school_id, processor_name, processor_department,
                    barcode, institutional_email, borrow_date, due_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), ?)""",
                (book["id"], borrower["id"], g.user["id"], borrower["school_id"], borrower["name"],
                 borrower["department"], g.user["school_id"], g.user["name"], g.user["department"],
                 book["barcode"], institutional_email, due_date),
            )
            connection.execute("UPDATE books SET available_quantity = available_quantity - 1 WHERE id = ?", (book["id"],))
        return jsonify({"message": "Book borrowed."}), 201
    except IntegrityError:
        return api_error("Unable to create the transaction.", 409)


@app.post("/api/transactions/<int:transaction_id>/return")
@roles_required("MIS Admin", "Librarian")
def return_book(transaction_id):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        transaction = connection.execute(
            "SELECT book_id, status FROM book_transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if not transaction:
            return api_error("Transaction not found.", 404)
        if transaction["status"] != "borrowed":
            return api_error("This book has already been returned.", 409)
        connection.execute(
            "UPDATE book_transactions SET status = 'returned', return_date = datetime('now', 'localtime') WHERE id = ?",
            (transaction_id,),
        )
        connection.execute(
            "UPDATE books SET available_quantity = available_quantity + 1 WHERE id = ?", (transaction["book_id"],)
        )
    return jsonify({"message": "Book returned."})


@app.get("/api/reports")
@roles_required("MIS Admin", "Librarian")
def report_transactions():
    try:
        rows = transaction_rows(
            request.args.get("q", "").strip(), request.args.get("from", ""), request.args.get("to", "")
        )
        return jsonify({"transactions": rows})
    except ValueError as error:
        return api_error(str(error))


@app.get("/api/dashboard")
@roles_required("MIS Admin", "Librarian")
def dashboard_data():
    today = manila_now()[:10]
    with get_connection() as connection:
        counts = connection.execute(
            """SELECT
                   (SELECT COUNT(*) FROM book_transactions WHERE status = 'borrowed') AS borrowed_books,
                   (SELECT COUNT(*) FROM book_transactions WHERE status = 'borrowed' AND due_date < ?) AS overdue_books,
                   (SELECT COUNT(*) FROM attendance_records a JOIN users u ON u.id = a.person_id
                    WHERE a.time_out IS NULL AND u.status = 'active') AS people_inside"""
        , (today,)).fetchone()
    return jsonify({"stats": dict(counts), "attendance": attendance_rows()[:40]})


def book_activity_rows(overdue_only=False):
    today = manila_now()[:10]
    sql = """
        SELECT t.id, COALESCE(NULLIF(t.borrower_school_id, ''), u.school_id, '') AS school_id,
               COALESCE(NULLIF(t.borrower_name, ''), u.name, '') AS borrower_name, b.barcode, b.title,
               t.borrow_date, t.due_date,
               CAST(julianday(?) - julianday(date(t.due_date)) AS INTEGER) AS days_overdue,
               CASE WHEN date(t.due_date) < ? THEN 'Overdue' ELSE 'Borrowed' END AS status
        FROM book_transactions t
        JOIN books b ON b.id = t.book_id
        LEFT JOIN users u ON u.id = t.borrower_id
        WHERE t.status = 'borrowed'
    """
    parameters = [today, today]
    if overdue_only:
        sql += " AND date(t.due_date) < ?"
        parameters.append(today)
    sql += " ORDER BY t.due_date, lower(COALESCE(NULLIF(t.borrower_name, ''), u.name, ''))"
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


@app.get("/api/dashboard/people-inside")
@roles_required("MIS Admin", "Librarian")
def dashboard_people_inside():
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT u.school_id, u.name, COALESCE(NULLIF(u.person_type, ''), u.role) AS role,
                      u.department, a.time_in, a.attendance_date
               FROM attendance_records a JOIN users u ON u.id = a.person_id
               WHERE a.time_out IS NULL AND u.status = 'active'
               ORDER BY a.time_in"""
        ).fetchall()
    return jsonify({"count": len(rows), "people": [dict(row) for row in rows]})


@app.get("/api/dashboard/overdue-books")
@roles_required("MIS Admin", "Librarian")
def dashboard_overdue_books():
    rows = book_activity_rows(overdue_only=True)
    return jsonify({"count": len(rows), "books": rows})


@app.get("/api/dashboard/borrowed-books")
@roles_required("MIS Admin", "Librarian")
def dashboard_borrowed_books():
    rows = book_activity_rows()
    return jsonify({"count": len(rows), "books": rows})


@app.get("/api/dashboard/access-logs")
@roles_required("MIS Admin", "Librarian")
def dashboard_access_logs():
    return jsonify({"records": attendance_rows()[:100]})


def attendance_api_response(event_type):
    data = request.get_json(silent=True) or {}
    try:
        school_id = clean_text(data, "school_id", 50)
    except ValueError as error:
        return api_error(str(error))
    timestamp = manila_now()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        person = connection.execute(
            """SELECT id, school_id, name, role, person_type, department, status, face_encoding
               FROM users WHERE school_id = ?""",
            (school_id,),
        ).fetchone()
        if not person or person["role"] in ("MIS Admin", "Librarian"):
            return api_error("No registered library person has that school ID.", 404)
        if person["status"] != "active":
            return api_error("Inactive people cannot record attendance.", 403)
        if not person["face_encoding"]:
            return api_error("This person has not completed face registration.", 409)
        active = connection.execute(
            "SELECT id FROM attendance_records WHERE person_id = ? AND time_out IS NULL",
            (person["id"],),
        ).fetchone()
        if event_type == "time_in":
            if active:
                return api_error("This person already has an active Time-in.", 409)
            connection.execute(
                """INSERT INTO attendance_records
                   (person_id, person_school_id, person_name, person_role, person_department,
                    time_in, attendance_date, source, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'face_recognition', 'inside')""",
                (person["id"], person["school_id"], person["name"], person["person_type"] or person["role"],
                 person["department"], timestamp, timestamp[:10]),
            )
        else:
            if not active:
                return api_error("This person has no active Time-in to close.", 409)
            connection.execute(
                """UPDATE attendance_records SET time_out = ?, status = 'completed'
                   WHERE id = ? AND time_out IS NULL""",
                (timestamp, active["id"]),
            )
    return jsonify({
        "event": event_type,
        "timestamp": timestamp,
        "person": {
            "school_id": person["school_id"],
            "name": person["name"],
            "role": person["person_type"] or person["role"],
            "department": person["department"],
        },
    }), 201


@app.get("/api/attendance")
@roles_required("MIS Admin", "Librarian")
def list_attendance():
    try:
        records = attendance_rows(
            request.args.get("q", "").strip(), request.args.get("from", ""), request.args.get("to", ""),
            request.args.get("role", "").strip(), request.args.get("department", "").strip(),
            request.args.get("event", "all").strip(),
        )
        return jsonify({"records": records})
    except ValueError as error:
        return api_error(str(error))


@app.get("/api/reports/attendance")
@roles_required("MIS Admin", "Librarian")
def attendance_report():
    return list_attendance()


@app.delete("/api/attendance/<int:attendance_id>")
@roles_required("MIS Admin")
def delete_attendance_record(attendance_id):
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM attendance_records WHERE id = ?", (attendance_id,))
        if not cursor.rowcount:
            return api_error("Attendance record not found.", 404)
    return jsonify({"message": "Attendance history record deleted."})


@app.post("/api/attendance/time-in")
@iot_required
def attendance_time_in():
    return attendance_api_response("time_in")


@app.post("/api/attendance/time-out")
@iot_required
def attendance_time_out():
    return attendance_api_response("time_out")


@app.get("/api/users")
@roles_required("MIS Admin")
def list_users():
    search = f"%{request.args.get('q', '').strip()}%"
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, school_id, name, email, role, department, status, created_at
               FROM users WHERE role IN ('MIS Admin', 'Librarian')
               AND (school_id LIKE ? OR name LIKE ? OR email LIKE ? OR role LIKE ?)
               ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, lower(name)""",
            (search, search, search, search),
        ).fetchall()
    return jsonify({"users": [dict(row) for row in rows]})


def validate_user(data, creating=False):
    school_id = clean_text(data, "school_id", 50)
    name = clean_text(data, "name")
    email = clean_text(data, "email", 254).lower()
    role = clean_text(data, "role", 30)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address.")
    if role not in ("MIS Admin", "Librarian", "Student", "Faculty"):
        raise ValueError("Choose a valid role.")
    password = str(data.get("password", ""))
    if creating and role in ("MIS Admin", "Librarian") and len(password) < 8:
        raise ValueError("MIS Admin and Librarian passwords must be at least 8 characters.")
    return {
        "school_id": school_id,
        "name": name,
        "email": email,
        "role": role,
        "department": str(data.get("department", "")).strip()[:100],
        "password": password,
    }


@app.post("/api/users")
@roles_required("MIS Admin")
def create_user():
    data = request.get_json(silent=True) or {}
    try:
        user = validate_user(data, creating=True)
        if user["role"] not in ("MIS Admin", "Librarian"):
            return api_error("Use People Information to add students, faculty, and other library visitors.")
        password_hash = generate_password_hash(user["password"]) if user["role"] in ("MIS Admin", "Librarian") else None
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO users (school_id, name, email, password_hash, role, department)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["school_id"], user["name"], user["email"], password_hash, user["role"], user["department"]),
            )
        return jsonify({"id": cursor.lastrowid, "message": "User added."}), 201
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        return api_error("School ID or email is already in use.", 409)


@app.patch("/api/users/<int:user_id>")
@roles_required("MIS Admin")
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    try:
        user = validate_user(data)
        fields = {
            "school_id": user["school_id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "department": user["department"],
            "status": "inactive" if data.get("status") == "inactive" else "active",
        }
        if user["password"]:
            if len(user["password"]) < 8:
                return api_error("Password must be at least 8 characters.")
            fields["password_hash"] = generate_password_hash(user["password"])
        if user_id == g.user["id"] and fields["status"] != "active":
            return api_error("You cannot deactivate your own account.", 409)
        with get_connection() as connection:
            current = connection.execute(
                "SELECT id, role, password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not current:
                return api_error("User not found.", 404)
            if current["role"] not in ("MIS Admin", "Librarian"):
                return api_error("Use People Information to update this record.", 404)
            if fields["role"] in ("MIS Admin", "Librarian") and not user["password"] and not current["password_hash"]:
                return api_error("Set a password before assigning a staff login role.")
            if current["role"] == "MIS Admin" and (fields["role"] != "MIS Admin" or fields["status"] != "active"):
                admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'MIS Admin' AND status = 'active'"
                ).fetchone()[0]
                if admins <= 1:
                    return api_error("Keep at least one active MIS Admin account.", 409)
            assignments = ", ".join(f"{field} = ?" for field in fields)
            connection.execute(
                f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id)
            )
        return jsonify({"message": "User updated."})
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        return api_error("School ID or email is already in use.", 409)


@app.delete("/api/users/<int:user_id>")
@roles_required("MIS Admin")
def deactivate_user(user_id):
    if user_id == g.user["id"]:
        return api_error("You cannot deactivate your own account.", 409)
    with get_connection() as connection:
        user = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["role"] not in ("MIS Admin", "Librarian"):
            return api_error("User not found.", 404)
        if user["role"] == "MIS Admin":
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'MIS Admin' AND status = 'active'"
            ).fetchone()[0]
            if admins <= 1:
                return api_error("Keep at least one active MIS Admin account.", 409)
        connection.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (user_id,))
    return jsonify({"message": "User deactivated."})


def delete_user_preserving_history(connection, user_id):
    recovery_rows = connection.execute(
        """SELECT id FROM password_recovery_requests
           WHERE user_id = ? AND status IN ('pending', 'approved')""",
        (user_id,),
    ).fetchall()
    for recovery_row in recovery_rows:
        connection.execute(
            """UPDATE password_recovery_requests
               SET status = 'expired', code_hash = '', token_hash = NULL,
                   reset_token_hash = NULL, reviewed_at = ?
               WHERE id = ?""",
            (manila_now(), recovery_row["id"]),
        )
        recovery_audit(connection, recovery_row["id"], "account_deleted", g.user["id"])
    connection.execute(
        "UPDATE book_transactions SET borrower_id = NULL WHERE borrower_id = ?",
        (user_id,),
    )
    connection.execute(
        "UPDATE book_transactions SET processed_by = NULL WHERE processed_by = ?",
        (user_id,),
    )
    connection.execute(
        "UPDATE attendance_records SET person_id = NULL WHERE person_id = ?",
        (user_id,),
    )
    cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cursor.rowcount > 0


def person_role_expression():
    return "COALESCE(NULLIF(person_type, ''), role)"


def validate_person(data):
    school_id = clean_text(data, "school_id", 50)
    name = clean_text(data, "name")
    person_role = clean_text(data, "role", 30)
    if person_role not in ("Student", "Faculty", "Staff", "Other"):
        raise ValueError("Choose Student, Faculty, Staff, or Other.")
    department = str(data.get("department", "")).strip()
    contact = str(data.get("contact", "")).strip()
    if len(department) > 100 or len(contact) > 80:
        raise ValueError("Department or contact information is too long.")
    status = str(data.get("status", "active")).strip().lower()
    if status not in ("active", "inactive"):
        raise ValueError("Status must be Active or Inactive.")
    return {
        "school_id": school_id,
        "name": name,
        "person_role": person_role,
        "role": person_role if person_role in ("Student", "Faculty") else "Student",
        "department": department,
        "contact": contact,
        "status": status,
    }


@app.get("/api/people")
@roles_required("MIS Admin")
def list_people():
    search = f"%{request.args.get('q', '').strip()}%"
    person_role = request.args.get("role", "").strip()
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT id, school_id, name, {person_role_expression()} AS role,
                       department, contact, status, created_at,
                       CASE WHEN face_image = '' THEN 0 ELSE 1 END AS has_face_image,
                       CASE WHEN face_encoding = '' THEN 0 ELSE 1 END AS face_registered
                FROM users
                WHERE role NOT IN ('MIS Admin', 'Librarian')
                  AND (school_id LIKE ? OR name LIKE ? OR department LIKE ? OR {person_role_expression()} LIKE ?)
                  AND (? = '' OR {person_role_expression()} = ?)
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, lower(name)""",
            (search, search, search, search, person_role, person_role),
        ).fetchall()
    return jsonify({"people": [dict(row) for row in rows]})


@app.post("/api/people")
@roles_required("MIS Admin")
def create_person():
    data = request.get_json(silent=True) or {}
    try:
        person = validate_person(data)
        email = person["school_id"].lower() + "@paterostechnologicalcollege.edu.ph"
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO users
                   (school_id, name, email, role, person_type, department, contact, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (person["school_id"], person["name"], email, person["role"], person["person_role"],
                 person["department"], person["contact"], person["status"]),
            )
        return jsonify({"id": cursor.lastrowid, "message": "People information saved."}), 201
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        return api_error("That School ID is already registered.", 409)


@app.put("/api/people/<int:person_id>")
@roles_required("MIS Admin")
def update_person(person_id):
    data = request.get_json(silent=True) or {}
    try:
        person = validate_person(data)
        with get_connection() as connection:
            current = connection.execute(
                "SELECT role FROM users WHERE id = ?", (person_id,)
            ).fetchone()
            if not current or current["role"] in ("MIS Admin", "Librarian"):
                return api_error("People record not found.", 404)
            connection.execute(
                """UPDATE users SET school_id = ?, name = ?, role = ?, person_type = ?,
                   department = ?, contact = ?, status = ? WHERE id = ?""",
                (person["school_id"], person["name"], person["role"], person["person_role"],
                 person["department"], person["contact"], person["status"], person_id),
            )
            if person["status"] == "inactive":
                timestamp = manila_now()
                connection.execute(
                    """UPDATE attendance_records SET time_out = ?, status = 'admin_closed'
                       WHERE person_id = ? AND time_out IS NULL""",
                    (timestamp, person_id),
                )
        return jsonify({"message": "People information updated."})
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        return api_error("That School ID is already registered.", 409)


# ---------------------------------------------------------------------------
# People Information: Excel import + School ID lookup
# ---------------------------------------------------------------------------
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_ERRORS_SHOWN = 50
MAX_HEADER_SCAN_ROWS = 20
MAX_IMPORT_SHEETS = 10
PERSON_ROLES = {
    "student": "Student", "students": "Student", "learner": "Student", "pupil": "Student",
    "faculty": "Faculty", "teacher": "Faculty", "instructor": "Faculty", "professor": "Faculty",
    "lecturer": "Faculty",
    "staff": "Staff", "employee": "Staff", "personnel": "Staff", "non-teaching": "Staff",
    "nonteaching": "Staff",
    "other": "Other", "visitor": "Other", "guest": "Other", "alumni": "Other", "alumnus": "Other",
}
ACTIVE_STATUS_WORDS = {"active", "regular", "irregular", "new", "transferee", "returnee",
                       "continuing", "enrolled", "officially enrolled"}
INACTIVE_STATUS_WORDS = {"inactive", "dropped", "withdrawn", "graduated", "leave of absence", "loa"}

# Column matching. Headers are compared after removing spaces and punctuation, so
# "ID No.", "Id no" and "ID_NO" are all "idno". Any column that is not matched is ignored.
EXACT_HEADERS = {
    "school_id": ("schoolid", "schoolidno", "schoolidnumber", "id", "idno", "idnumber", "idnum",
                  "studentid", "studentno", "studentnumber", "facultyid", "employeeid", "employeeno"),
    "name": ("name", "fullname", "completename", "personname", "studentname", "facultyname",
             "employeename"),
    "last_name": ("lastname", "surname", "familyname", "lname"),
    "first_name": ("firstname", "givenname", "fname"),
    "middle_name": ("middlename", "middleinitial", "mname", "mi"),
    "suffix": ("suffix", "extensionname", "nameextension", "extension"),
    "role": ("role", "persontype", "usertype", "type", "category", "position", "designation"),
    "department": ("department", "dept", "program", "course", "courseprogram", "courseorprogram",
                   "programcourse", "college", "departmentcourse"),
    "contact": ("contact", "contactno", "contactnumber", "contactinformation", "phone", "phoneno",
                "phonenumber", "mobile", "mobileno", "mobilenumber", "cellphone", "cellphonenumber",
                "cp", "cpno", "telephone"),
    "status": ("status", "accountstatus", "personstatus", "enrollmentstatus", "enrolmentstatus",
               "studentstatus"),
}
# Fallback when no exact match exists: the header merely contains one of these words.
KEYWORD_HEADERS = (
    ("school_id", ("schoolid", "studentid", "studentno", "studentnumber", "employeeid",
                   "facultyid", "idnumber", "idno")),
    ("department", ("department", "dept", "program", "course", "college")),
    ("contact", ("contact", "mobile", "phone", "telephone", "cellphone")),
    ("status", ("status",)),
    ("role", ("role", "persontype", "usertype")),
    ("name", ("fullname", "completename", "studentname", "facultyname", "employeename", "personname")),
)
# Headers containing these words are never used by the keyword fallback
# (e.g. "Father's Name", "Civil Status", "Emergency Contact No.", "Email Address").
EXCLUDED_HEADER_WORDS = (
    "father", "mother", "guardian", "parent", "spouse", "emergency", "maiden", "civil", "marital",
    "email", "address", "relationship", "birth", "yearlevel", "semester", "section", "schoolyear",
    "schoolname", "collegename", "contactperson",
)
FIELD_LABELS = {
    "school_id": "School ID", "name": "Name", "last_name": "Name (last name)",
    "first_name": "Name (first name)", "middle_name": "Name (middle name)", "suffix": "Name (suffix)",
    "role": "Role", "department": "Department", "contact": "Contact", "status": "Status",
}
XLSX_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SQL_SCHOOL_ID_KEY = "REPLACE(REPLACE(REPLACE(REPLACE(LOWER(school_id), '-', ''), ' ', ''), '_', ''), '.', '')"


def import_status(raw):
    """Map enrolment wording (REGULAR, IRREGULAR, DROPPED...) to the system's Active / Inactive."""
    word = raw.strip().lower()
    if not word or word in ACTIVE_STATUS_WORDS:
        return "active"
    if word in INACTIVE_STATUS_WORDS:
        return "inactive"
    return raw


def school_id_key(value):
    """'23BSIT-0177', '23bsit 0177' and '23BSIT0177' are treated as the same School ID."""
    return re.sub(r"[\s\-_.]", "", str(value)).lower()


def tidy(value):
    return " ".join(str(value).split())


def clean_contact(value):
    value = tidy(value)
    # Excel drops the leading zero of numeric mobile numbers (9171234567 -> 09171234567).
    return "0" + value if re.fullmatch(r"9\d{9}", value) else value


def spreadsheet_number_text(number):
    """Excel stores numbers as floats; 20229512.0 should be imported as 20229512."""
    if isinstance(number, bool):
        return "TRUE" if number else "FALSE"
    if isinstance(number, float):
        return str(int(number)) if number.is_integer() else repr(number)
    return str(number)


def xlsx_column_index(reference):
    letters = re.match(r"[A-Za-z]+", reference or "")
    if not letters:
        return None
    index = 0
    for char in letters.group(0).upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def parse_xlsx_sheet(archive, path, shared):
    rows = []
    for row in ET.fromstring(archive.read(path)).iter(XLSX_MAIN + "row"):
        values = {}
        next_index = 0
        for cell in row.findall(XLSX_MAIN + "c"):
            index = xlsx_column_index(cell.get("r"))
            index = next_index if index is None else index
            next_index = index + 1
            kind = cell.get("t")
            raw = cell.find(XLSX_MAIN + "v")
            if kind == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(XLSX_MAIN + "t"))
            elif raw is None or raw.text is None:
                text = ""
            elif kind == "s":
                try:
                    text = shared[int(raw.text)]
                except (ValueError, IndexError):
                    text = ""
            elif kind == "b":
                text = "TRUE" if raw.text.strip() == "1" else "FALSE"
            elif kind in ("str", "e"):
                text = raw.text
            else:
                try:
                    text = spreadsheet_number_text(float(raw.text))
                except ValueError:
                    text = raw.text
            values[index] = text.strip()
        width = (max(values) + 1) if values else 0
        rows.append((int(row.get("r") or len(rows) + 1), [values.get(i, "") for i in range(width)]))
    return rows


def read_xlsx_sheets(content):
    """Return [(sheet name, rows)] for an .xlsx file using only the standard library."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError("This file is not a valid Excel (.xlsx) workbook.")
    with archive:
        if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
            raise ValueError("This workbook is too large to import.")
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("This file is not a valid Excel (.xlsx) workbook.")

        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).iter(XLSX_MAIN + "si"):
                parts = []
                for child in item:
                    if child.tag == XLSX_MAIN + "t":
                        parts.append(child.text or "")
                    elif child.tag == XLSX_MAIN + "r":
                        parts.extend(node.text or "" for node in child.findall(XLSX_MAIN + "t"))
                shared.append("".join(parts))

        sheets = []
        try:
            targets = {}
            for relation in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
                target = relation.get("Target", "")
                targets[relation.get("Id")] = (
                    target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
                )
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            for sheet in workbook.find(XLSX_MAIN + "sheets"):
                path = targets.get(sheet.get(XLSX_REL + "id"))
                if path in names:
                    sheets.append((sheet.get("name") or path, path))
        except (KeyError, AttributeError, TypeError, ET.ParseError):
            sheets = []
        if not sheets:
            sheets = [(Path(name).stem, name) for name in sorted(names)
                      if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)]
        if not sheets:
            raise ValueError("The workbook has no worksheet to import.")
        return [(name, parse_xlsx_sheet(archive, path, shared)) for name, path in sheets[:MAX_IMPORT_SHEETS]]


def read_xls_sheets(content):
    """Return [(sheet name, rows)] for a legacy .xls file (needs the optional xlrd package)."""
    try:
        import xlrd
    except ImportError:
        raise ValueError(
            "Legacy .xls files need the 'xlrd' package (run: pip install xlrd). "
            "Alternatively, open the file in Excel and save it as .xlsx."
        )
    try:
        workbook = xlrd.open_workbook(file_contents=content)
    except Exception:
        raise ValueError("This file is not a valid Excel (.xls) workbook.")
    sheets = []
    for sheet in workbook.sheets()[:MAX_IMPORT_SHEETS]:
        rows = []
        for number in range(sheet.nrows):
            cells = [spreadsheet_number_text(value) if isinstance(value, (int, float)) else str(value).strip()
                     for value in sheet.row_values(number)]
            rows.append((number + 1, cells))
        sheets.append((sheet.name, rows))
    return sheets


def read_spreadsheet_sheets(content):
    if content[:4] == b"PK\x03\x04":
        return read_xlsx_sheets(content)
    if content[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        return read_xls_sheets(content)
    raise ValueError("This file is not a valid Excel workbook (.xlsx or .xls).")


def match_import_columns(titles):
    """Match header cells to the people fields. Returns {field: column index}."""
    keys = [re.sub(r"[^a-z0-9]", "", title.lower()) for title in titles]
    mapping = {}
    for index, key in enumerate(keys):  # pass 1: exact header names (leftmost column wins)
        if not key:
            continue
        for field, aliases in EXACT_HEADERS.items():
            if key in aliases and field not in mapping:
                mapping[field] = index
                break
    used = set(mapping.values())
    for index, key in enumerate(keys):  # pass 2: header contains a known word
        if not key or index in used or any(word in key for word in EXCLUDED_HEADER_WORDS):
            continue
        for field, words in KEYWORD_HEADERS:
            if field not in mapping and any(word in key for word in words):
                mapping[field] = index
                used.add(index)
                break
    return mapping


def has_required_columns(mapping):
    has_name = "name" in mapping or ("last_name" in mapping and "first_name" in mapping)
    return "school_id" in mapping and has_name


def find_import_header(rows):
    for position, (number, cells) in enumerate(rows[:MAX_HEADER_SCAN_ROWS]):
        mapping = match_import_columns(cells)
        if has_required_columns(mapping):
            return number, mapping, cells, rows[position + 1:]
    return None


@app.post("/api/people/import")
@roles_required("MIS Admin")
def import_people():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return api_error("Choose an Excel file (.xlsx or .xls) to upload.")
    if not upload.filename.lower().endswith((".xlsx", ".xls")):
        return api_error("Only Excel files (.xlsx or .xls) can be imported.")
    content = upload.read(MAX_COVER_SIZE + 1)
    if len(content) > MAX_COVER_SIZE:
        return api_error("The uploaded file is larger than the 5 MB limit.", 413)
    try:
        sheets = read_spreadsheet_sheets(content)
    except (ValueError, ET.ParseError) as error:
        return api_error(str(error) if isinstance(error, ValueError) else "This Excel file could not be read.")

    found = None
    sheet_name = ""
    for sheet_name, rows in sheets:
        found = find_import_header(rows)
        if found:
            break
    if not found:
        return api_error(
            "Could not find the people columns in this file. A header row needs a School ID column "
            "(ID No., Student ID...) and a Name column (or First Name and Last Name). "
            "Role, Department / Program, Contact and Status are optional. "
            "Sheets checked: " + ", ".join(name for name, _ in sheets) + "."
        )
    header_row, columns, header_cells, data_rows = found

    def cell(cells, field):
        index = columns.get(field)
        return cells[index] if index is not None and index < len(cells) else ""

    def person_name(cells):
        if "name" in columns:
            return tidy(cell(cells, "name"))
        given = tidy(" ".join(part for part in (cell(cells, "first_name"), cell(cells, "middle_name"),
                                                 cell(cells, "suffix")) if part))
        last = tidy(cell(cells, "last_name"))
        return f"{last}, {given}" if last and given else (last or given)

    data_rows = [(number, cells) for number, cells in data_rows if any(str(value).strip() for value in cells)]
    if not data_rows:
        return api_error("The Excel file has no people rows to import.")
    if len(data_rows) > MAX_IMPORT_ROWS:
        return api_error(f"Import up to {MAX_IMPORT_ROWS} rows at a time.")

    used_columns = set(columns.values())
    matched_columns = [
        {"field": FIELD_LABELS[field], "column": header_cells[columns[field]]}
        for field in FIELD_LABELS if field in columns
    ]
    ignored_columns = [title for index, title in enumerate(header_cells)
                       if title.strip() and index not in used_columns]

    added = updated = unchanged = 0
    errors = []
    seen_ids = set()
    with get_connection() as connection:
        known_people = {
            school_id_key(row["school_id"]): row
            for row in connection.execute(
                "SELECT id, school_id, name, role, person_type, department, contact, status FROM users"
            )
        }
        for number, cells in data_rows:
            raw_role = (tidy(cell(cells, "role")) if "role" in columns else "") or "Student"
            record = {
                "school_id": tidy(cell(cells, "school_id")),
                "name": person_name(cells),
                "role": PERSON_ROLES.get(raw_role.lower(), raw_role),
                "department": tidy(cell(cells, "department")),
                "contact": clean_contact(cell(cells, "contact")),
                "status": import_status(tidy(cell(cells, "status"))),
            }
            try:
                person = validate_person(record)
            except ValueError as error:
                errors.append({"row": number, "school_id": record["school_id"], "reason": str(error)})
                continue
            key = school_id_key(person["school_id"])
            if not key:
                errors.append({"row": number, "school_id": person["school_id"],
                               "reason": "School ID must contain letters or numbers."})
                continue
            if key in seen_ids:
                errors.append({"row": number, "school_id": person["school_id"],
                               "reason": "Duplicate School ID in this file (first occurrence was used)."})
                continue
            seen_ids.add(key)

            existing = known_people.get(key)
            if existing:
                if existing["role"] in ("MIS Admin", "Librarian"):
                    errors.append({"row": number, "school_id": person["school_id"],
                                   "reason": "This School ID belongs to a system account."})
                    continue
                current = (existing["name"], existing["person_type"] or existing["role"],
                           existing["department"], existing["contact"], existing["status"])
                incoming = (person["name"], person["person_role"], person["department"],
                            person["contact"], person["status"])
                if current == incoming:
                    unchanged += 1
                    continue
                # Face image / encoding are intentionally left untouched.
                connection.execute(
                    """UPDATE users SET name = ?, role = ?, person_type = ?, department = ?,
                       contact = ?, status = ? WHERE id = ?""",
                    (person["name"], person["role"], person["person_role"], person["department"],
                     person["contact"], person["status"], existing["id"]),
                )
                if person["status"] == "inactive" and existing["status"] != "inactive":
                    connection.execute(
                        """UPDATE attendance_records SET time_out = ?, status = 'admin_closed'
                           WHERE person_id = ? AND time_out IS NULL""",
                        (manila_now(), existing["id"]),
                    )
                updated += 1
                continue
            email = person["school_id"].lower() + "@paterostechnologicalcollege.edu.ph"
            try:
                connection.execute(
                    """INSERT INTO users
                       (school_id, name, email, role, person_type, department, contact, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (person["school_id"], person["name"], email, person["role"], person["person_role"],
                     person["department"], person["contact"], person["status"]),
                )
                added += 1
            except IntegrityError:
                errors.append({"row": number, "school_id": person["school_id"],
                               "reason": "This School ID or its generated email is already in use."})

    return jsonify({
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": len(errors),
        "errors": errors[:MAX_IMPORT_ERRORS_SHOWN],
        "more_errors": max(0, len(errors) - MAX_IMPORT_ERRORS_SHOWN),
        "sheet": sheet_name,
        "matched_columns": matched_columns,
        "ignored_columns": ignored_columns,
        "message": "Excel import finished. Face images are not imported; upload them manually in People Information.",
    })


@app.get("/api/people/lookup")
@roles_required("MIS Admin")
def lookup_person():
    school_id = request.args.get("school_id", "").strip()
    if not school_id or not school_id_key(school_id):
        return api_error("Enter a School ID.")
    with get_connection() as connection:
        row = connection.execute(
            f"""SELECT id, school_id, name, role AS account_role, {person_role_expression()} AS role,
                       department, contact, status,
                       CASE WHEN face_image = '' THEN 0 ELSE 1 END AS has_face_image,
                       CASE WHEN face_encoding = '' THEN 0 ELSE 1 END AS face_registered
                FROM users WHERE {SQL_SCHOOL_ID_KEY} = ?""",
            (school_id_key(school_id),),
        ).fetchone()
    if not row:
        return jsonify({"person": None})
    person = dict(row)
    if person.pop("account_role") in ("MIS Admin", "Librarian"):
        return jsonify({"person": None, "system_account": True})
    return jsonify({"person": person})


@app.delete("/api/people/<int:person_id>")
@roles_required("MIS Admin")
def deactivate_person(person_id):
    with get_connection() as connection:
        person = connection.execute(
            "SELECT role FROM users WHERE id = ?", (person_id,)
        ).fetchone()
        if not person or person["role"] in ("MIS Admin", "Librarian"):
            return api_error("People record not found.", 404)
        timestamp = manila_now()
        connection.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (person_id,))
        connection.execute(
            """UPDATE attendance_records SET time_out = ?, status = 'admin_closed'
               WHERE person_id = ? AND time_out IS NULL""",
            (timestamp, person_id),
        )
    return jsonify({"message": "Person deactivated. Any open attendance session was closed by an administrator."})


@app.delete("/api/people/<int:person_id>/permanent")
@roles_required("MIS Admin")
def permanently_delete_person(person_id):
    with get_connection() as connection:
        person = connection.execute(
            "SELECT role, status, face_image FROM users WHERE id = ?", (person_id,)
        ).fetchone()
        if not person or person["role"] in ("MIS Admin", "Librarian"):
            return api_error("People record not found.", 404)
        if person["status"] != "inactive":
            return api_error("Deactivate the person before permanently deleting the record.", 409)
        if not delete_user_preserving_history(connection, person_id):
            return api_error("People record not found.", 404)
    delete_stored_file("face", person["face_image"])
    return jsonify({"message": "People information permanently deleted."})


def uploaded_face_extension(image_data):
    return cover_extension(image_data)


def write_file_durably(path, data):
    """Write to a temp file, flush to disk, then rename so a crash never leaves a partial image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with open(temporary, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def save_person_face(person_id, upload):
    """Save the image permanently and register the face encoding in the same request.

    Returns (result, error). result["registered"] is False only when the recognition
    engine is unavailable on the server; the camera station then registers it as before.
    """
    image_data = upload.stream.read(MAX_COVER_SIZE + 1)
    if len(image_data) > MAX_COVER_SIZE:
        return None, api_error("Face images must be 5 MB or smaller.", 413)
    extension = uploaded_face_extension(image_data)
    if not extension:
        return None, api_error("Choose a valid PNG, JPEG, GIF, or WebP image.")

    with get_connection() as connection:
        person = connection.execute("SELECT role FROM users WHERE id = ?", (person_id,)).fetchone()
    if not person or person["role"] in ("MIS Admin", "Librarian"):
        return None, api_error("People record not found.", 404)

    encoding_json = ""
    try:
        encoding = face_enrollment.encode_face_image(image_data)
        encoding_json = json.dumps(encoding, separators=(",", ":"))
    except face_enrollment.FaceImageError as error:
        return None, api_error(str(error), 422)
    except face_enrollment.FaceEngineUnavailable as error:
        app.logger.warning("Server-side face registration unavailable: %s", error)

    filename = secrets.token_hex(16) + extension
    try:
        store_file("face", filename, image_data)
    except (OSError, *DatabaseError):
        app.logger.exception("Could not store face image")
        return None, api_error("The face image could not be stored on the server. Please try again.", 500)
    with get_connection() as connection:
        person = connection.execute(
            "SELECT face_image, role FROM users WHERE id = ?", (person_id,)
        ).fetchone()
        if not person or person["role"] in ("MIS Admin", "Librarian"):
            delete_stored_file("face", filename)
            return None, api_error("People record not found.", 404)
        connection.execute(
            "UPDATE users SET face_image = ?, face_encoding = ? WHERE id = ?",
            (filename, encoding_json, person_id),
        )
    delete_stored_file("face", person["face_image"])
    return {"filename": filename, "registered": bool(encoding_json)}, None


def register_pending_faces_on_server():
    """Register stored face images that have no encoding yet (e.g. uploaded before this update)."""
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """SELECT id, face_image FROM users
                   WHERE face_image != '' AND face_encoding = ''
                     AND role NOT IN ('MIS Admin', 'Librarian')"""
            ).fetchall()
        for row in rows:
            stored = read_stored_file("face", row["face_image"])
            if not stored:
                continue
            try:
                encoding = face_enrollment.encode_face_image(stored[0])
            except face_enrollment.FaceImageError as error:
                app.logger.info("Face for person %s not registered: %s", row["id"], error)
                continue
            with get_connection() as connection:
                connection.execute(
                    "UPDATE users SET face_encoding = ? WHERE id = ? AND face_image = ? AND face_encoding = ''",
                    (json.dumps(encoding, separators=(",", ":")), row["id"], row["face_image"]),
                )
    except face_enrollment.FaceEngineUnavailable as error:
        app.logger.warning("Pending face registration skipped: %s", error)
    except Exception:
        app.logger.exception("Pending face registration failed")


@app.post("/api/people/<int:person_id>/face")
@roles_required("MIS Admin")
def upload_person_face(person_id):
    upload = request.files.get("face_image")
    if not upload:
        return api_error("Choose a face image to upload.")
    result, error = save_person_face(person_id, upload)
    if error:
        return error
    if result["registered"]:
        return jsonify({"message": "Face image saved and face registration completed.", "face_registered": True})
    return jsonify({
        "message": "Face image saved. Face registration is pending IoT processing.",
        "face_registered": False,
    })


@app.get("/api/people/<int:person_id>/face-image")
@roles_required("MIS Admin")
def get_person_face(person_id):
    with get_connection() as connection:
        person = connection.execute("SELECT face_image FROM users WHERE id = ?", (person_id,)).fetchone()
    if not person or not person["face_image"]:
        abort(404)
    return stored_file_response("face", person["face_image"])


@app.delete("/api/people/<int:person_id>/face")
@roles_required("MIS Admin")
def reset_person_face(person_id):
    with get_connection() as connection:
        person = connection.execute("SELECT face_image, role FROM users WHERE id = ?", (person_id,)).fetchone()
        if not person or person["role"] in ("MIS Admin", "Librarian"):
            return api_error("People record not found.", 404)
        connection.execute("UPDATE users SET face_image = '', face_encoding = '' WHERE id = ?", (person_id,))
    delete_stored_file("face", person["face_image"])
    return jsonify({"message": "Face image and recognition data removed."})


@app.get("/api/system-users")
@roles_required("MIS Admin")
def list_system_users():
    role = request.args.get("role", "").strip()
    if role and role not in ("MIS Admin", "Librarian"):
        return api_error("Choose MIS Admin or Librarian.")
    search = f"%{request.args.get('q', '').strip()}%"
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, school_id, name, email, role, department, status, created_at, last_login_at
               FROM users WHERE role IN ('MIS Admin', 'Librarian')
               AND (? = '' OR role = ?) AND (name LIKE ? OR email LIKE ? OR school_id LIKE ?)
               ORDER BY role, lower(name)""",
            (role, role, search, search, search),
        ).fetchall()
    return jsonify({"users": [dict(row) for row in rows]})


@app.post("/api/system-users")
@roles_required("MIS Admin")
def create_system_user():
    data = request.get_json(silent=True) or {}
    try:
        user = validate_user(data, creating=True)
        if user["role"] not in ("MIS Admin", "Librarian"):
            return api_error("System accounts must be MIS Admin or Librarian.")
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO users (school_id, name, email, password_hash, role, department)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["school_id"], user["name"], user["email"], generate_password_hash(user["password"]),
                 user["role"], user["department"]),
            )
        return jsonify({"id": cursor.lastrowid, "message": "System account added."}), 201
    except (ValueError, TypeError) as error:
        return api_error(str(error))
    except IntegrityError:
        return api_error("School ID or email is already in use.", 409)


@app.put("/api/system-users/<int:user_id>")
@roles_required("MIS Admin")
def update_system_user(user_id):
    data = request.get_json(silent=True) or {}
    data["role"] = str(data.get("role", "")).strip()
    if data["role"] not in ("MIS Admin", "Librarian"):
        return api_error("System accounts must be MIS Admin or Librarian.")
    with get_connection() as connection:
        row = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["role"] not in ("MIS Admin", "Librarian"):
            return api_error("System account not found.", 404)
    return update_user(user_id)


@app.delete("/api/system-users/<int:user_id>")
@roles_required("MIS Admin")
def deactivate_system_user(user_id):
    with get_connection() as connection:
        row = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["role"] not in ("MIS Admin", "Librarian"):
            return api_error("System account not found.", 404)
    return deactivate_user(user_id)


@app.delete("/api/system-users/<int:user_id>/permanent")
@roles_required("MIS Admin")
def permanently_delete_system_user(user_id):
    if user_id == g.user["id"]:
        return api_error("You cannot delete your own account.", 409)
    with get_connection() as connection:
        user = connection.execute(
            "SELECT role, status FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user or user["role"] not in ("MIS Admin", "Librarian"):
            return api_error("System account not found.", 404)
        if user["status"] != "inactive":
            return api_error("Deactivate the system account before permanently deleting it.", 409)
        if not delete_user_preserving_history(connection, user_id):
            return api_error("System account not found.", 404)
    return jsonify({"message": "System account permanently deleted."})


@app.get("/api/iot/face-enrollments")
@iot_required
def iot_face_enrollments():
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT id, school_id, name, {person_role_expression()} AS role, department
                FROM users WHERE status = 'active' AND role NOT IN ('MIS Admin', 'Librarian')
                  AND face_image != '' AND face_encoding = '' ORDER BY id"""
        ).fetchall()
    return jsonify({"people": [dict(row) for row in rows]})


@app.get("/api/iot/people/<int:person_id>/face-image")
@iot_required
def iot_person_face_image(person_id):
    with get_connection() as connection:
        person = connection.execute(
            "SELECT face_image FROM users WHERE id = ? AND status = 'active'", (person_id,)
        ).fetchone()
    if not person or not person["face_image"]:
        abort(404)
    return stored_file_response("face", person["face_image"])


@app.put("/api/iot/people/<int:person_id>/face-encoding")
@iot_required
def iot_store_face_encoding(person_id):
    data = request.get_json(silent=True) or {}
    encoding = data.get("encoding")
    if not isinstance(encoding, list) or len(encoding) != 128:
        return api_error("Face encoding must contain 128 numeric values.")
    try:
        values = [float(value) for value in encoding]
    except (TypeError, ValueError):
        return api_error("Face encoding values must be numeric.")
    if not all(math.isfinite(value) for value in values):
        return api_error("Face encoding contains an invalid number.")
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude < 0.000001:
        return api_error("Face encoding cannot be empty.")
    normalized = [value / magnitude for value in values]
    with get_connection() as connection:
        cursor = connection.execute(
            """UPDATE users SET face_encoding = ? WHERE id = ? AND status = 'active'
               AND role NOT IN ('MIS Admin', 'Librarian') AND face_image != ''""",
            (json.dumps(normalized, separators=(",", ":")), person_id),
        )
        if cursor.rowcount == 0:
            return api_error("Active person with an uploaded face image not found.", 404)
    return jsonify({"message": "Face recognition data registered."})


@app.get("/api/iot/face-templates")
@iot_required
def iot_face_templates():
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT u.id, u.school_id, u.name, {person_role_expression()} AS role,
                       u.department, u.status, u.face_encoding,
                       EXISTS(SELECT 1 FROM attendance_records a WHERE a.person_id = u.id AND a.time_out IS NULL) AS is_inside
                FROM users u WHERE u.role NOT IN ('MIS Admin', 'Librarian')
                  AND u.face_encoding != '' ORDER BY u.id"""
        ).fetchall()
    templates = []
    for row in rows:
        item = dict(row)
        item["encoding"] = json.loads(item.pop("face_encoding"))
        item["is_inside"] = bool(item["is_inside"])
        templates.append(item)
    return jsonify({"people": templates})


@app.get("/healthz")
def health_check():
    with get_connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return jsonify({"status": "ok"})


@app.get("/")
def welcome_page():
    return send_from_directory(PROJECT_ROOT, "welcome.html")


@app.route("/<path:requested_path>", methods=["GET", "HEAD"])
def frontend_files(requested_path):
    lower_path = requested_path.lower()
    if (lower_path.startswith((".", "backend/", "face_recognition_iot/", "uploads/", "models/"))
            or lower_path.startswith("smart_library.db")
            or lower_path in {"render.yaml", "requirements.txt", "build.sh", "runtime.txt"}):
        abort(404)
    file_path = (PROJECT_ROOT / requested_path).resolve()
    if not file_path.is_relative_to(PROJECT_ROOT) or not file_path.is_file():
        abort(404)

    normalized = requested_path.replace("\\", "/")
    if normalized.lower().endswith(".html"):
        role = g.user["role"] if g.user else None
        if normalized.startswith("MIS_ADMIN/") and role != "MIS Admin":
            return redirect("/LogIn.html" if not role else "/LIBRARIAN/dashboard.html")
        if normalized.startswith("LIBRARIAN/") and role != "Librarian":
            return redirect("/LogIn.html" if not role else "/MIS_ADMIN/dashboard.html")
        if role and normalized == "LogIn.html":
            return redirect("/MIS_ADMIN/dashboard.html" if role == "MIS Admin" else "/LIBRARIAN/dashboard.html")
        if normalized in {"LogIn.html", "welcome.html", "EmailVerification.html", "VerificationCode.html", "ChangePassword.html"}:
            return send_from_directory(PROJECT_ROOT, requested_path)
    return send_from_directory(PROJECT_ROOT, requested_path)


def handle_database_error(error):
    app.logger.exception("Database request failed: %s", error)
    return jsonify({"error": "A database error occurred. Please try again."}), 500


for _database_error in DatabaseError:
    app.register_error_handler(_database_error, handle_database_error)


@app.errorhandler(413)
def handle_large_upload(error):
    return api_error("The uploaded file is larger than the 5 MB limit.", 413)


def start_pending_face_registration():
    import threading
    threading.Thread(target=register_pending_faces_on_server, name="face-backfill", daemon=True).start()


start_pending_face_registration()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")