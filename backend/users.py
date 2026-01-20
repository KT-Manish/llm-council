"""SQLite-based user storage for authentication."""

import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from .config import USERS_DB


def ensure_db():
    """Ensure the database and tables exist."""
    Path(USERS_DB).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    ensure_db()
    conn = sqlite3.connect(USERS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def create_user(email: str, password_hash: str, name: str, is_admin: bool = False) -> Dict[str, Any]:
    """
    Create a new user.

    Args:
        email: User email (unique)
        password_hash: Bcrypt-hashed password
        name: Display name
        is_admin: Whether user has admin privileges

    Returns:
        Created user dict (without password_hash)
    """
    conn = get_connection()
    cursor = conn.cursor()

    user_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    try:
        cursor.execute(
            """
            INSERT INTO users (id, email, password_hash, name, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, password_hash, name, is_admin, created_at)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"User with email {email} already exists")

    conn.close()

    return {
        "id": user_id,
        "email": email,
        "name": name,
        "is_admin": is_admin,
        "created_at": created_at
    }


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a user by ID.

    Args:
        user_id: User identifier

    Returns:
        User dict (without password_hash) or None if not found
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, email, name, is_admin, created_at FROM users WHERE id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    return dict(row)


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Get a user by email (includes password_hash for authentication).

    Args:
        email: User email

    Returns:
        User dict (with password_hash) or None if not found
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, email, password_hash, name, is_admin, created_at FROM users WHERE email = ?",
        (email,)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    return dict(row)


def list_users() -> List[Dict[str, Any]]:
    """
    List all users.

    Returns:
        List of user dicts (without password_hash)
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, email, name, is_admin, created_at FROM users ORDER BY created_at DESC"
    )
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def delete_user(user_id: str) -> bool:
    """
    Delete a user by ID.

    Args:
        user_id: User identifier

    Returns:
        True if user was deleted, False if not found
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def user_exists() -> bool:
    """Check if any users exist in the database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()

    return count > 0
