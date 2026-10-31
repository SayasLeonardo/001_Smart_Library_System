"""SQLite setup and starter records for the Smart Library System."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import generate_password_hash


# On Render, SMART_LIBRARY_DATA_DIR points at the persistent disk (e.g. /var/data) so the
# database and uploaded images survive deploys and restarts. When it is not set, the
# original local-development locations are used unchanged.
_DATA_DIR_SETTING = os.environ.get("SMART_LIBRARY_DATA_DIR", "").strip()
DATA_DIR = Path(_DATA_DIR_SETTING).resolve() if _DATA_DIR_SETTING else None

if DATA_DIR:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH = DATA_DIR / "smart_library.db"
else:
    DATABASE_PATH = Path(__file__).resolve().parent.parent / "smart_library.db"


@contextmanager
def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# Optional starter catalog. Each entry is (book_number, title, author, category, published_date, quantity).
# The original file referenced an undefined `books` list, which crashed first-time startup
# on an empty database. An empty list keeps a fresh deployment bootable.
SEED_BOOKS = []


def seed_required_staff_accounts(connection):
    required_accounts = [
        {
            "school_id": "MIS-001",
            "name": "PTC MIS",
            "email": "mis@paterostechnologicalcollege.edu.ph",
            "password": "admin123",
            "role": "MIS Admin",
            "department": "MIS",
        },
        {
            "school_id": "LIB-001",
            "name": "PTC Librarian",
            "email": "librarian@paterostechnologicalcollege.edu.ph",
            "password": "librarian123",
            "role": "Librarian",
            "department": "Library",
        },
    ]

    for account in required_accounts:
        record = connection.execute(
            "SELECT id FROM users WHERE lower(email) = ? OR school_id = ? ORDER BY id LIMIT 1",
            (account["email"].lower(), account["school_id"]),
        ).fetchone()
        if record:
            connection.execute(
                """UPDATE users
                   SET school_id = ?, name = ?, email = ?,
                       password_hash = COALESCE(password_hash, ?), role = ?, department = ?
                   WHERE id = ?""",
                (
                    account["school_id"],
                    account["name"],
                    account["email"].lower(),
                    generate_password_hash(account["password"]),
                    account["role"],
                    account["department"],
                    record["id"],
                ),
            )
        else:
            connection.execute(
                """INSERT INTO users (school_id, name, email, password_hash, role, department, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'active')""",
                (
                    account["school_id"],
                    account["name"],
                    account["email"].lower(),
                    generate_password_hash(account["password"]),
                    account["role"],
                    account["department"],
                ),
            )

def initialize_database():
    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                role TEXT NOT NULL CHECK (role IN ('MIS Admin', 'Librarian', 'Student', 'Faculty')),
                department TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_number TEXT NOT NULL UNIQUE,
                barcode TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                category TEXT NOT NULL,
                published_date TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
                cover_image TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS book_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id),
                borrower_id INTEGER REFERENCES users(id),
                processed_by INTEGER REFERENCES users(id),
                borrower_school_id TEXT NOT NULL DEFAULT '',
                borrower_name TEXT NOT NULL DEFAULT '',
                borrower_department TEXT NOT NULL DEFAULT '',
                processor_school_id TEXT NOT NULL DEFAULT '',
                processor_name TEXT NOT NULL DEFAULT '',
                processor_department TEXT NOT NULL DEFAULT '',
                barcode TEXT NOT NULL DEFAULT '',
                institutional_email TEXT NOT NULL DEFAULT '',
                borrow_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                return_date TEXT,
                status TEXT NOT NULL DEFAULT 'borrowed' CHECK (status IN ('borrowed', 'returned')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER REFERENCES users(id),
                person_school_id TEXT NOT NULL DEFAULT '',
                person_name TEXT NOT NULL DEFAULT '',
                person_role TEXT NOT NULL DEFAULT '',
                person_department TEXT NOT NULL DEFAULT '',
                time_in TEXT NOT NULL,
                time_out TEXT,
                attendance_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'face_recognition',
                status TEXT NOT NULL DEFAULT 'inside' CHECK (status IN ('inside', 'completed', 'admin_closed')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS password_recovery_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                token_hash TEXT,
                reset_token_hash TEXT,
                verify_attempts INTEGER NOT NULL DEFAULT 0,
                verified_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'completed')),
                requested_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                approved_at TEXT,
                reviewed_at TEXT,
                used_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_active_recovery_request_per_user
                ON password_recovery_requests(user_id) WHERE status IN ('pending', 'approved');
            CREATE INDEX IF NOT EXISTS recovery_requests_email_idx ON password_recovery_requests(email, status, expires_at);
            CREATE TABLE IF NOT EXISTS password_recovery_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER REFERENCES password_recovery_requests(id) ON DELETE SET NULL,
                actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_recovery_rate_limits (
                action TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                window_start TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (action, key_hash, window_start)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_attendance_per_person
                ON attendance_records(person_id) WHERE time_out IS NULL AND person_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS attendance_time_in_date ON attendance_records(time_in);
            """
        )

        book_columns = {row["name"] for row in connection.execute("PRAGMA table_info(books)")}
        if "cover_image" not in book_columns:
            connection.execute("ALTER TABLE books ADD COLUMN cover_image TEXT NOT NULL DEFAULT ''")
        if "book_number" not in book_columns:
            connection.execute("ALTER TABLE books ADD COLUMN book_number TEXT NOT NULL DEFAULT ''")
            legacy_books = connection.execute("SELECT id, barcode FROM books ORDER BY id").fetchall()
            for book in legacy_books:
                book_number = book["barcode"]
                barcode = f"SL{book['id']:010d}"
                suffix = 1
                while connection.execute(
                    "SELECT 1 FROM books WHERE barcode = ? AND id != ?", (barcode, book["id"])
                ).fetchone():
                    barcode = f"SL{book['id']:08d}{suffix:02d}"
                    suffix += 1
                connection.execute(
                    "UPDATE books SET book_number = ?, barcode = ? WHERE id = ?",
                    (book_number, barcode, book["id"]),
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS books_book_number_unique ON books(book_number)"
        )

        transaction_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(book_transactions)")
        }
        if "barcode" not in transaction_columns:
            connection.execute("ALTER TABLE book_transactions ADD COLUMN barcode TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """UPDATE book_transactions
                   SET barcode = (SELECT barcode FROM books WHERE books.id = book_transactions.book_id)"""
            )
        if "institutional_email" not in transaction_columns:
            connection.execute(
                "ALTER TABLE book_transactions ADD COLUMN institutional_email TEXT NOT NULL DEFAULT ''"
            )
            connection.execute(
                """UPDATE book_transactions
                   SET institutional_email = (SELECT email FROM users WHERE users.id = book_transactions.borrower_id)"""
            )

        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        for column, definition in (
            ("person_type", "TEXT NOT NULL DEFAULT ''"),
            ("contact", "TEXT NOT NULL DEFAULT ''"),
            ("face_image", "TEXT NOT NULL DEFAULT ''"),
            ("face_encoding", "TEXT NOT NULL DEFAULT ''"),
            ("last_login_at", "TEXT"),
        ):
            if column not in user_columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        recovery_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(password_recovery_requests)")
        }
        legacy_recovery_schema = "reset_token_hash" not in recovery_columns
        for column, definition in (
            ("reset_token_hash", "TEXT"),
            ("verify_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("verified_at", "TEXT"),
        ):
            if column not in recovery_columns:
                connection.execute(
                    f"ALTER TABLE password_recovery_requests ADD COLUMN {column} {definition}"
                )
        if legacy_recovery_schema:
            connection.execute(
                """UPDATE password_recovery_requests
                   SET status = 'expired', code_hash = '', token_hash = NULL,
                       reset_token_hash = NULL, reviewed_at = datetime('now')
                   WHERE status IN ('pending', 'approved')"""
            )
        recovery_user_id = next(
            row for row in connection.execute("PRAGMA table_info(password_recovery_requests)")
            if row["name"] == "user_id"
        )
        if recovery_user_id["notnull"]:
            connection.execute("DROP TABLE IF EXISTS password_recovery_requests_new")
            connection.execute(
                """CREATE TABLE password_recovery_requests_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    email TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    token_hash TEXT,
                    reset_token_hash TEXT,
                    verify_attempts INTEGER NOT NULL DEFAULT 0,
                    verified_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'completed')),
                    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at TEXT NOT NULL,
                    approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    approved_at TEXT,
                    reviewed_at TEXT,
                    used_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            connection.execute(
                """INSERT INTO password_recovery_requests_new (
                       id, user_id, email, code_hash, token_hash, reset_token_hash,
                       verify_attempts, verified_at, status, requested_at, expires_at,
                       approved_by, approved_at, reviewed_at, used_at, created_at
                   )
                   SELECT id, user_id, email, code_hash, token_hash, reset_token_hash,
                          verify_attempts, verified_at, status, requested_at, expires_at,
                          approved_by, approved_at, reviewed_at, used_at, created_at
                   FROM password_recovery_requests"""
            )
            connection.execute("DROP TABLE password_recovery_requests")
            connection.execute(
                "ALTER TABLE password_recovery_requests_new RENAME TO password_recovery_requests"
            )
            connection.execute(
                """CREATE UNIQUE INDEX one_active_recovery_request_per_user
                   ON password_recovery_requests(user_id) WHERE status IN ('pending', 'approved')"""
            )
            connection.execute(
                "CREATE INDEX recovery_requests_email_idx ON password_recovery_requests(email, status, expires_at)"
            )
        audit_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(password_recovery_audit)"
        ).fetchall()
        if any(
            row["from"] in {"request_id", "actor_id"} and row["on_delete"] != "SET NULL"
            for row in audit_foreign_keys
        ):
            connection.execute("DROP TABLE IF EXISTS password_recovery_audit_new")
            connection.execute(
                """CREATE TABLE password_recovery_audit_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER REFERENCES password_recovery_requests(id) ON DELETE SET NULL,
                    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT INTO password_recovery_audit_new (id, request_id, actor_id, action, created_at)
                   SELECT id, request_id, actor_id, action, created_at FROM password_recovery_audit"""
            )
            connection.execute("DROP TABLE password_recovery_audit")
            connection.execute(
                "ALTER TABLE password_recovery_audit_new RENAME TO password_recovery_audit"
            )

        transaction_columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(book_transactions)")
        }
        transaction_snapshots = {
            "borrower_school_id", "borrower_name", "borrower_department",
            "processor_school_id", "processor_name", "processor_department",
        }
        if (any(transaction_columns[column]["notnull"] for column in ("borrower_id", "processed_by"))
                or not transaction_snapshots.issubset(transaction_columns)):
            connection.execute("DROP TABLE IF EXISTS book_transactions_new")
            connection.execute(
                """CREATE TABLE book_transactions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL REFERENCES books(id),
                    borrower_id INTEGER REFERENCES users(id),
                    processed_by INTEGER REFERENCES users(id),
                    borrower_school_id TEXT NOT NULL DEFAULT '',
                    borrower_name TEXT NOT NULL DEFAULT '',
                    borrower_department TEXT NOT NULL DEFAULT '',
                    processor_school_id TEXT NOT NULL DEFAULT '',
                    processor_name TEXT NOT NULL DEFAULT '',
                    processor_department TEXT NOT NULL DEFAULT '',
                    barcode TEXT NOT NULL DEFAULT '',
                    institutional_email TEXT NOT NULL DEFAULT '',
                    borrow_date TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    return_date TEXT,
                    status TEXT NOT NULL DEFAULT 'borrowed' CHECK (status IN ('borrowed', 'returned')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            connection.execute(
                """INSERT INTO book_transactions_new (
                       id, book_id, borrower_id, processed_by,
                       borrower_school_id, borrower_name, borrower_department,
                       processor_school_id, processor_name, processor_department,
                       barcode, institutional_email, borrow_date, due_date, return_date, status, created_at
                   )
                   SELECT t.id, t.book_id, t.borrower_id, t.processed_by,
                          COALESCE(borrower.school_id, ''), COALESCE(borrower.name, ''),
                          COALESCE(borrower.department, ''), COALESCE(processor.school_id, ''),
                          COALESCE(processor.name, ''), COALESCE(processor.department, ''),
                          t.barcode, t.institutional_email, t.borrow_date, t.due_date,
                          t.return_date, t.status, t.created_at
                   FROM book_transactions t
                   LEFT JOIN users borrower ON borrower.id = t.borrower_id
                   LEFT JOIN users processor ON processor.id = t.processed_by"""
            )
            connection.execute("DROP TABLE book_transactions")
            connection.execute("ALTER TABLE book_transactions_new RENAME TO book_transactions")

        attendance_columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(attendance_records)")
        }
        attendance_snapshots = {"person_school_id", "person_name", "person_role", "person_department"}
        if (attendance_columns["person_id"]["notnull"]
                or not attendance_snapshots.issubset(attendance_columns)):
            connection.execute("DROP TABLE IF EXISTS attendance_records_new")
            connection.execute(
                """CREATE TABLE attendance_records_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id INTEGER REFERENCES users(id),
                    person_school_id TEXT NOT NULL DEFAULT '',
                    person_name TEXT NOT NULL DEFAULT '',
                    person_role TEXT NOT NULL DEFAULT '',
                    person_department TEXT NOT NULL DEFAULT '',
                    time_in TEXT NOT NULL,
                    time_out TEXT,
                    attendance_date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'face_recognition',
                    status TEXT NOT NULL DEFAULT 'inside' CHECK (status IN ('inside', 'completed', 'admin_closed')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            connection.execute(
                """INSERT INTO attendance_records_new (
                       id, person_id, person_school_id, person_name, person_role, person_department,
                       time_in, time_out, attendance_date, source, status, created_at
                   )
                   SELECT a.id, a.person_id, COALESCE(person.school_id, ''), COALESCE(person.name, ''),
                          COALESCE(NULLIF(person.person_type, ''), person.role, ''),
                          COALESCE(person.department, ''), a.time_in, a.time_out,
                          a.attendance_date, a.source, a.status, a.created_at
                   FROM attendance_records a
                   LEFT JOIN users person ON person.id = a.person_id"""
            )
            connection.execute("DROP TABLE attendance_records")
            connection.execute("ALTER TABLE attendance_records_new RENAME TO attendance_records")

        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_open_attendance_per_person
               ON attendance_records(person_id) WHERE time_out IS NULL AND person_id IS NOT NULL"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS attendance_time_in_date ON attendance_records(time_in)")

        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            users = [
                ("MIS-001", "PTC MIS", "mis@paterostechnologicalcollege.edu.ph", "admin123", "MIS Admin", "MIS"),
                ("LIB-001", "PTC Librarian", "librarian@paterostechnologicalcollege.edu.ph", "librarian123", "Librarian", "Library"),
            ]
            for school_id, name, email, password, role, department in users:
                password_hash = generate_password_hash(password) if password else None
                connection.execute(
                    """INSERT INTO users (school_id, name, email, password_hash, role, department)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (school_id, name, email, password_hash, role, department),
                )

        seed_required_staff_accounts(connection)

        book_count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if book_count == 0 and SEED_BOOKS:
            connection.executemany(
                """INSERT INTO books
                   (book_number, barcode, title, author, category, published_date, quantity, available_quantity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (book[0], f"SL{index:010d}", *book[1:], book[-1])
                    for index, book in enumerate(SEED_BOOKS, start=1)
                ],
            )