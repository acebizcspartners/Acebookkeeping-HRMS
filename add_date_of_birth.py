#!/usr/bin/env python3
"""
Migration script to add date_of_birth column to User table in Neon database
Run this once to update the production database
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env file")
    exit(1)

# Convert postgres:// to postgresql:// if needed
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

try:
    engine = create_engine(DATABASE_URL, echo=True)

    with engine.connect() as conn:
        # Check if date_of_birth column exists
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='user' AND column_name='date_of_birth'
        """))

        if not result.fetchone():
            print("Adding date_of_birth column to user table...")
            conn.execute(text("""
                ALTER TABLE "user"
                ADD COLUMN date_of_birth DATE
            """))
            conn.commit()
            print("✓ Successfully added date_of_birth column!")
        else:
            print("✓ date_of_birth column already exists")

except Exception as e:
    print(f"ERROR: {e}")
    exit(1)
