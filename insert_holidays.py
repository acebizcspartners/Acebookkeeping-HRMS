#!/usr/bin/env python3
"""
Insert holidays into Neon PostgreSQL database
"""

import os
from datetime import datetime
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL')

# Holiday data for 2025 and 2026
holidays_data = [
    ("Happy New Year", "2025-01-01", "New Year"),
    ("Republic Day", "2025-01-26", "National Holiday"),
    ("Maha Shivratri", "2025-02-26", "Festival"),
    ("Holi", "2025-03-14", "Festival"),
    ("Id-ul-Fitr", "2025-03-31", "Festival"),
    ("Good Friday", "2025-04-18", "Festival"),
    ("Raksha Bandhan", "2025-08-09", "Festival"),
    ("Independence Day", "2025-08-15", "National Holiday"),
    ("Janmashtami", "2025-08-16", "Festival"),
    ("Dussehra", "2025-10-02", "Festival"),
    ("Mahatma Gandhi Birthday", "2025-10-02", "National Holiday"),
    ("Diwali", "2025-10-20", "Festival"),
    ("Goverdhan Puja", "2025-10-21", "Festival"),
    ("Bhai Dooj", "2025-10-22", "Festival"),
    ("Guru Nanak's Birthday", "2025-11-05", "Festival"),
    ("Christmas Day", "2025-12-25", "Festival"),

    # 2026 holidays
    ("Happy New Year", "2026-01-01", "New Year"),
    ("Republic Day", "2026-01-26", "National Holiday"),
    ("Maha Shivratri", "2026-02-16", "Festival"),
    ("Holi", "2026-03-03", "Festival"),
    ("Id-ul-Fitr", "2026-03-21", "Festival"),
    ("Good Friday", "2026-04-03", "Festival"),
    ("Raksha Bandhan", "2026-08-10", "Festival"),
    ("Independence Day", "2026-08-15", "National Holiday"),
    ("Janmashtami", "2026-09-04", "Festival"),
    ("Dussehra", "2026-10-20", "Festival"),
    ("Mahatma Gandhi Birthday", "2026-10-02", "National Holiday"),
    ("Diwali", "2026-11-08", "Festival"),
    ("Goverdhan Puja", "2026-11-09", "Festival"),
    ("Bhai Dooj", "2026-11-10", "Festival"),
    ("Guru Nanak's Birthday", "2026-11-24", "Festival"),
    ("Christmas Day", "2026-12-25", "Festival"),
]

try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    print("[INFO] Connected to Neon PostgreSQL")

    # Check if holidays already exist
    cursor.execute("SELECT COUNT(*) FROM holiday;")
    count = cursor.fetchone()[0]

    if count > 0:
        print(f"[INFO] {count} holidays already exist. Skipping insertion.")
        cursor.close()
        conn.close()
        exit(0)

    # Insert holidays
    inserted = 0
    for holiday_name, holiday_date, holiday_type in holidays_data:
        try:
            cursor.execute(
                """INSERT INTO holiday (holiday_name, holiday_date, holiday_type, description)
                   VALUES (%s, %s, %s, %s)""",
                (holiday_name, holiday_date, holiday_type, f"{holiday_name} - {holiday_type}")
            )
            inserted += 1
        except Exception as e:
            print(f"[SKIP] {holiday_name} ({holiday_date}): {str(e)}")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\n[SUCCESS] {inserted} holidays inserted successfully!")

except Exception as e:
    print(f"[ERROR] Failed to insert holidays: {str(e)}")
    exit(1)
