import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), 'reports.db'))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                logo_path TEXT,
                primary_color TEXT DEFAULT '#1a73e8',
                secondary_color TEXT DEFAULT '#ffffff',
                accent_color TEXT DEFAULT '#34a853',
                text_color TEXT DEFAULT '#1a1a1a',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                platform TEXT DEFAULT 'meta',
                date_range TEXT,
                source_file TEXT,
                content TEXT,
                raw_data TEXT,
                status TEXT DEFAULT 'draft',
                embed_token TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS report_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
                image_path TEXT NOT NULL,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


# --- Client helpers ---

def get_all_clients():
    with get_db() as conn:
        return conn.execute("SELECT * FROM clients ORDER BY name").fetchall()


def get_client(client_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()


def create_client(name, logo_path, primary_color, secondary_color, accent_color, text_color):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO clients (name, logo_path, primary_color, secondary_color, accent_color, text_color) VALUES (?, ?, ?, ?, ?, ?)",
            (name, logo_path, primary_color, secondary_color, accent_color, text_color)
        )
        return cur.lastrowid


def update_client(client_id, name, logo_path, primary_color, secondary_color, accent_color, text_color):
    with get_db() as conn:
        conn.execute(
            "UPDATE clients SET name=?, logo_path=?, primary_color=?, secondary_color=?, accent_color=?, text_color=? WHERE id=?",
            (name, logo_path, primary_color, secondary_color, accent_color, text_color, client_id)
        )


def delete_client(client_id):
    with get_db() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


# --- Report helpers ---

def get_all_reports():
    with get_db() as conn:
        return conn.execute("""
            SELECT r.*, c.name as client_name, c.primary_color
            FROM reports r
            LEFT JOIN clients c ON r.client_id = c.id
            ORDER BY r.created_at DESC
        """).fetchall()


def get_report(report_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT r.*, c.name as client_name, c.logo_path,
                   c.primary_color, c.secondary_color, c.accent_color, c.text_color
            FROM reports r
            LEFT JOIN clients c ON r.client_id = c.id
            WHERE r.id = ?
        """, (report_id,)).fetchone()


def get_report_by_token(token):
    with get_db() as conn:
        return conn.execute("""
            SELECT r.*, c.name as client_name, c.logo_path,
                   c.primary_color, c.secondary_color, c.accent_color, c.text_color
            FROM reports r
            LEFT JOIN clients c ON r.client_id = c.id
            WHERE r.embed_token = ?
        """, (token,)).fetchone()


def create_report(client_id, title, platform, date_range, source_file, content, raw_data):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO reports (client_id, title, platform, date_range, source_file, content, raw_data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, title, platform, date_range, source_file, content, raw_data)
        )
        return cur.lastrowid


def update_report_content(report_id, content):
    with get_db() as conn:
        conn.execute(
            "UPDATE reports SET content=?, updated_at=? WHERE id=?",
            (content, datetime.now(), report_id)
        )


def publish_report(report_id, embed_token):
    with get_db() as conn:
        conn.execute(
            "UPDATE reports SET status='published', embed_token=?, updated_at=? WHERE id=?",
            (embed_token, datetime.now(), report_id)
        )


def delete_report(report_id):
    with get_db() as conn:
        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))


def get_client_reports(client_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM reports WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,)
        ).fetchall()


# --- Image helpers ---

def add_report_image(report_id, image_path, caption=''):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO report_images (report_id, image_path, caption) VALUES (?, ?, ?)",
            (report_id, image_path, caption)
        )
        return cur.lastrowid


def get_report_images(report_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM report_images WHERE report_id = ? ORDER BY created_at",
            (report_id,)
        ).fetchall()


def delete_image(image_id):
    with get_db() as conn:
        img = conn.execute("SELECT image_path FROM report_images WHERE id = ?", (image_id,)).fetchone()
        conn.execute("DELETE FROM report_images WHERE id = ?", (image_id,))
        return img['image_path'] if img else None


# --- Stats ---

def get_stats():
    with get_db() as conn:
        clients_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        reports_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        published_count = conn.execute("SELECT COUNT(*) FROM reports WHERE status='published'").fetchone()[0]
        recent = conn.execute("""
            SELECT r.title, r.status, r.created_at, r.platform, c.name as client_name, r.id
            FROM reports r LEFT JOIN clients c ON r.client_id = c.id
            ORDER BY r.created_at DESC LIMIT 5
        """).fetchall()
        return {
            'clients': clients_count,
            'reports': reports_count,
            'published': published_count,
            'recent': recent
        }
