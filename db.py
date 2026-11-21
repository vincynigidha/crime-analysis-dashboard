"""
Central DB connection helper (PyMySQL - pure Python, no compiler needed on Windows).
Every module (auth, dashboard, category, crime, trend, location, reports)
imports get_db() from here so there's a single connection-per-request pattern.
"""
import os
import pymysql
import pymysql.cursors
from flask import g

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    user=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", ""),   # set the DB_PASSWORD environment variable
    database=os.environ.get("DB_NAME", "crime_analysis"),
    cursorclass=pymysql.cursors.DictCursor,  # rows as dicts, not tuples
    autocommit=False,
)


def init_db(app):
    """Register a teardown so each request's DB connection is closed automatically."""
    app.teardown_appcontext(close_db)


def get_db():
    """Return the DB connection for the current request, opening one if needed."""
    if "db" not in g:
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
