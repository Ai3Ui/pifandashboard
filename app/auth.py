#!/usr/bin/python3
"""Password hashing and short-lived dashboard session management."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import time

PBKDF2_ITERATIONS = 350_000
SESSION_TTL = 12 * 60 * 60
MAX_SESSIONS = 64


def password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return {
        "version": 1,
        "algorithm": "pbkdf2-sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }


def _password_record_parts(record):
    if not isinstance(record, dict):
        raise ValueError("authentication record must be an object")
    try:
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["digest"], validate=True)
        iterations = record["iterations"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("authentication record is malformed") from error
    if (
        record.get("version") != 1
        or record.get("algorithm") != "pbkdf2-sha256"
        or type(iterations) is not int
        or not 100_000 <= iterations <= 1_000_000
        or not 8 <= len(salt) <= 64
        or not 16 <= len(expected) <= 64
    ):
        raise ValueError("authentication record is invalid")
    return salt, expected, iterations


def load_password_record(path):
    """Read and structurally validate a small, regular auth file without following links."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or not 1 <= file_stat.st_size <= 4096:
            raise ValueError("authentication file must be a small regular file")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = None
            record = json.load(handle)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _password_record_parts(record)
    return record, file_stat.st_mtime_ns


def verify_password(password, record):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return False
    try:
        salt, expected, iterations = _password_record_parts(record)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_auth_file(path, force=False):
    existing_gid = None
    if os.path.lexists(path) and not os.path.islink(path):
        existing_gid = os.stat(path, follow_symlinks=False).st_gid
    if os.path.lexists(path) and not force:
        raise FileExistsError(path)
    if os.path.islink(path):
        raise RuntimeError(f"refusing to replace symlink: {path}")
    password = secrets.token_urlsafe(15)
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".pifan-auth-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(password_record(password), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
        if existing_gid is not None and hasattr(os, "chown"):
            os.chown(path, -1, existing_gid)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return password


class SessionStore:
    def __init__(self, auth_file, ttl=SESSION_TTL):
        self.auth_file = auth_file
        self.ttl = ttl
        self._sessions = {}
        self._auth_mtime = None
        self._lock = threading.Lock()

    @staticmethod
    def _token_key(token):
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _load_record(self):
        return load_password_record(self.auth_file)

    def login(self, password):
        record, mtime = self._load_record()
        if not verify_password(password, record):
            return None
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            if self._auth_mtime != mtime:
                self._sessions.clear()
                self._auth_mtime = mtime
            self._purge(now)
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=self._sessions.get)
                self._sessions.pop(oldest, None)
            self._sessions[self._token_key(token)] = now + self.ttl
        return token

    def valid(self, token):
        if not token:
            return False
        try:
            mtime = os.stat(self.auth_file, follow_symlinks=False).st_mtime_ns
        except OSError:
            return False
        now = time.monotonic()
        key = self._token_key(token)
        with self._lock:
            if self._auth_mtime is not None and self._auth_mtime != mtime:
                self._sessions.clear()
                self._auth_mtime = mtime
                return False
            self._purge(now)
            expiry = self._sessions.get(key, 0)
            if expiry <= now:
                self._sessions.pop(key, None)
                return False
            return True

    def logout(self, token):
        with self._lock:
            self._sessions.pop(self._token_key(token), None)

    def _purge(self, now):
        expired = [key for key, expiry in self._sessions.items() if expiry <= now]
        for key in expired:
            self._sessions.pop(key, None)


def main():
    parser = argparse.ArgumentParser(description="Create or reset the dashboard password")
    parser.add_argument("command", choices=("create", "reset", "validate"))
    parser.add_argument("path")
    arguments = parser.parse_args()
    if arguments.command == "validate":
        load_password_record(arguments.path)
        return
    password = create_auth_file(arguments.path, force=arguments.command == "reset")
    print(password)


if __name__ == "__main__":
    main()
