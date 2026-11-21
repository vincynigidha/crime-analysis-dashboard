"""
Run this once to create your first admin login.
Usage: python create_admin.py
"""
import os
from werkzeug.security import generate_password_hash
import pymysql

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")   # set the DB_PASSWORD environment variable
DB_NAME = os.environ.get("DB_NAME", "crime_analysis")

username = input("Choose admin username: ").strip()
password = input("Choose admin password: ").strip()
full_name = input("Full name: ").strip()
email = input("Email: ").strip()

password_hash = generate_password_hash(password)

conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
cur = conn.cursor()
cur.execute(
    "INSERT INTO users (username, password_hash, full_name, email) VALUES (%s, %s, %s, %s)",
    (username, password_hash, full_name, email)
)
conn.commit()
cur.close()
conn.close()

print(f"Admin user '{username}' created successfully.")
