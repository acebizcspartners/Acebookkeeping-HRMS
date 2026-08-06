#!/usr/bin/env python3
"""
Migration script to add profile columns to user table in Neon PostgreSQL
Run this once to update the database schema
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from psycopg2 import sql

# Load environment variables
load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env file")
    sys.exit(1)

# Parse connection string
try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    print("[INFO] Connected to Neon PostgreSQL")

    # Add columns to user table
    alter_statements = [
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS phone VARCHAR(20);",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS address TEXT;",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS city VARCHAR(50);",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS emergency_contact VARCHAR(100);",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS emergency_phone VARCHAR(20);",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS date_of_joining DATE;",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS designation VARCHAR(100);",
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS reporting_manager VARCHAR(100);",
    ]

    for statement in alter_statements:
        try:
            cursor.execute(statement)
            print(f"[OK] {statement}")
        except Exception as e:
            print(f"[SKIP] {statement} - {str(e)}")

    # Create holiday table
    create_holiday = """
    CREATE TABLE IF NOT EXISTS holiday (
        id SERIAL PRIMARY KEY,
        holiday_name VARCHAR(100) NOT NULL,
        holiday_date DATE NOT NULL,
        description TEXT,
        holiday_type VARCHAR(50) DEFAULT 'National',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    cursor.execute(create_holiday)
    print("[OK] Holiday table created")

    conn.commit()
    cursor.close()
    conn.close()

    print("\n[SUCCESS] Database schema updated successfully!")
    print("[INFO] Now you can run the app without schema errors")

except Exception as e:
    print(f"[ERROR] Database migration failed: {str(e)}")
    sys.exit(1)
