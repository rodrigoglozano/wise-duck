# AI usage disclosure: Claude (Anthropic) helped explain SQL/sqlite3
# concepts, sped up development, and drafted/reviewed the comments in this file.
# The `users`/`messages` schema and the daily-cap query logic were designed
# by me.

import sqlite3

DATABASE_PATH = "project.db"


# Open a new connection to project.db.
# row_factory = sqlite3.Row lets us read columns by name (row["email"])
# instead of by position (row[2]), which makes the route code below
# much easier to read.
def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


# Create the `users` and `messages` tables if they don't exist yet.
# Safe to call every time the app starts — CREATE TABLE IF NOT EXISTS
# is a no-op once a table is already there, so it won't wipe existing
# accounts or history on a restart.
def init_db():
    connection = get_db()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    # A permanent log of every real exchange, separate from
    # session["history"] (which only lives for the current login and is
    # never read back). This table exists purely for the 50/day cap and
    # future instructor analysis — the AI is never re-fed rows from here.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            module TEXT NOT NULL,
            message TEXT NOT NULL,
            sent_by TEXT NOT NULL,
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    connection.commit()
    connection.close()


# Count this student's own questions already logged today.
# Only sent_by='student' rows count toward the cap — each row's
# matching AI reply is a separate row but not a separate "question".
# SQLite's date(timestamp) = date('now') keeps this to the current
# calendar day, so the count (and the cap) resets automatically at
# midnight without any extra cleanup code.
def count_todays_student_messages(user_id):
    connection = get_db()
    row = connection.execute(
        """
        SELECT COUNT(*) AS count FROM messages
        WHERE user_id = ? AND sent_by = 'student' AND date(timestamp) = date('now')
        """,
        (user_id,),
    ).fetchone()
    connection.close()
    return row["count"]


# Write one full Q&A turn to `messages` as two rows.
# Call this ONLY after ask_ai() has already returned successfully
# (see app.py's try/except/else) — a failed Gemini call should never
# reach this function, so it's never logged and never counts against
# the student's daily cap.
def log_exchange(user_id, module, student_message, ai_reply):
    connection = get_db()
    connection.execute(
        "INSERT INTO messages (user_id, module, message, sent_by) VALUES (?, ?, ?, 'student')",
        (user_id, module, student_message),
    )
    connection.execute(
        "INSERT INTO messages (user_id, module, message, sent_by) VALUES (?, ?, ?, 'ai')",
        (user_id, module, ai_reply),
    )
    connection.commit()
    connection.close()
