#!/usr/bin/env python3
"""
Migration script to add missing profile columns to User table in Neon database
Columns: date_of_birth, gender, blood_group, state, postal_code, country,
          pan_number, aadhar_number, bank_account, ifsc_code
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

# Columns to add with their SQL definitions
COLUMNS_TO_ADD = {
    'date_of_birth': 'DATE',
    'gender': 'VARCHAR(20)',
    'blood_group': 'VARCHAR(10)',
    'state': 'VARCHAR(50)',
    'postal_code': 'VARCHAR(20)',
    'country': 'VARCHAR(50)',
    'pan_number': 'VARCHAR(20)',
    'aadhar_number': 'VARCHAR(20)',
    'bank_account': 'VARCHAR(50)',
    'ifsc_code': 'VARCHAR(20)'
}

try:
    engine = create_engine(DATABASE_URL, echo=True)

    with engine.connect() as conn:
        for column_name, column_type in COLUMNS_TO_ADD.items():
            # Check if column exists
            result = conn.execute(text(f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='user' AND column_name='{column_name}'
            """))

            if not result.fetchone():
                print(f"Adding {column_name} column...")
                conn.execute(text(f"""
                    ALTER TABLE "user"
                    ADD COLUMN {column_name} {column_type}
                """))
                print(f"✓ {column_name} added successfully")
            else:
                print(f"✓ {column_name} already exists")

        conn.commit()
        print("\n✓ All profile columns are now available!")

except Exception as e:
    print(f"ERROR: {e}")
    exit(1)
