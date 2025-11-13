#!/usr/bin/env python3
"""
Migration script to move data from files to PostgreSQL database
Run this once after setting up the database to migrate existing data
"""

import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from database import get_db_connection, init_db, check_db_connection
from workout_parser import parse_workout_entries

load_dotenv()

BASE_DIR = Path(__file__).parent
WORKOUT_LOG = BASE_DIR / "workouts.md"
THEMES_LOG = BASE_DIR / "themes.json"
USAGE_LOG = BASE_DIR / "usage.json"
FEEDBACK_LOG = BASE_DIR / "feedback.json"

def get_workout_key(date, text):
    """Generate a unique key for a workout entry"""
    import hashlib
    key = f"{date}:{text[:100]}"
    return hashlib.md5(key.encode()).hexdigest()

def migrate_workouts():
    """Migrate workouts from workouts.md to database"""
    if not WORKOUT_LOG.exists():
        print("No workouts.md file found, skipping workout migration")
        return 0
    
    print("Migrating workouts...")
    content = WORKOUT_LOG.read_text()
    if not content.strip():
        print("  workouts.md is empty, skipping")
        return 0
    
    workouts = parse_workout_entries(content)
    print(f"  Found {len(workouts)} workouts to migrate")
    
    migrated = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for workout in workouts:
                try:
                    cur.execute("""
                        INSERT INTO workouts (date, text)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (workout.get('date', ''), workout.get('text', '')))
                    if cur.rowcount > 0:
                        migrated += 1
                except Exception as e:
                    print(f"  Error migrating workout {workout.get('date', 'unknown')}: {e}")
    
    print(f"  ✓ Migrated {migrated} workouts")
    return migrated

def migrate_themes():
    """Migrate themes from themes.json to database"""
    if not THEMES_LOG.exists():
        print("No themes.json file found, skipping theme migration")
        return 0
    
    print("Migrating themes...")
    try:
        themes = json.loads(THEMES_LOG.read_text())
    except:
        print("  Error reading themes.json, skipping")
        return 0
    
    print(f"  Found {len(themes)} themes to migrate")
    
    migrated = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for workout_key, theme in themes.items():
                try:
                    cur.execute("""
                        INSERT INTO themes (workout_key, theme)
                        VALUES (%s, %s)
                        ON CONFLICT (workout_key) DO NOTHING
                    """, (workout_key, theme))
                    if cur.rowcount > 0:
                        migrated += 1
                except Exception as e:
                    print(f"  Error migrating theme {workout_key}: {e}")
    
    print(f"  ✓ Migrated {migrated} themes")
    return migrated

def migrate_usage():
    """Migrate usage statistics from usage.json to database"""
    if not USAGE_LOG.exists():
        print("No usage.json file found, skipping usage migration")
        return 0
    
    print("Migrating usage statistics...")
    try:
        usage = json.loads(USAGE_LOG.read_text())
    except:
        print("  Error reading usage.json, skipping")
        return 0
    
    daily = usage.get('daily', {})
    print(f"  Found {len(daily)} days of usage data")
    
    migrated = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for date_str, data in daily.items():
                try:
                    # Parse date string
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                    cur.execute("""
                        INSERT INTO usage (date, input_tokens, output_tokens, cost, requests)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (date) DO NOTHING
                    """, (
                        date_obj,
                        data.get('input_tokens', 0),
                        data.get('output_tokens', 0),
                        data.get('cost', 0.0),
                        data.get('requests', 0)
                    ))
                    if cur.rowcount > 0:
                        migrated += 1
                except Exception as e:
                    print(f"  Error migrating usage for {date_str}: {e}")
    
    print(f"  ✓ Migrated {migrated} days of usage data")
    return migrated

def migrate_feedback():
    """Migrate feedback from feedback.json to database"""
    if not FEEDBACK_LOG.exists():
        print("No feedback.json file found, skipping feedback migration")
        return 0
    
    print("Migrating feedback...")
    try:
        feedback_list = json.loads(FEEDBACK_LOG.read_text())
    except:
        print("  Error reading feedback.json, skipping")
        return 0
    
    print(f"  Found {len(feedback_list)} feedback entries to migrate")
    
    migrated = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for entry in feedback_list:
                try:
                    # Parse timestamp
                    timestamp_str = entry.get('timestamp', '')
                    if isinstance(timestamp_str, str):
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    else:
                        timestamp = datetime.now()
                    
                    # Get metadata
                    metadata = entry.get('metadata', {})
                    if not metadata and 'suggestion' in entry:
                        # Old format - convert to new format
                        metadata = {
                            'suggestion': entry.get('suggestion', ''),
                            'feedback': entry.get('feedback', '')
                        }
                    
                    cur.execute("""
                        INSERT INTO feedback (text, timestamp, user_agent, metadata)
                        VALUES (%s, %s, %s, %s)
                    """, (
                        entry.get('text', entry.get('feedback', '')),
                        timestamp,
                        entry.get('user_agent', ''),
                        json.dumps(metadata) if metadata else None
                    ))
                    migrated += 1
                except Exception as e:
                    print(f"  Error migrating feedback entry: {e}")
    
    print(f"  ✓ Migrated {migrated} feedback entries")
    return migrated

def main():
    """Run all migrations"""
    print("=" * 60)
    print("Database Migration Script")
    print("=" * 60)
    print()
    
    # Check database connection
    if not check_db_connection():
        print("❌ Database connection failed!")
        print("Make sure DATABASE_URL or POSTGRES_URL is set in your environment")
        return
    
    print("✓ Database connection successful")
    print()
    
    # Initialize database tables
    print("Initializing database tables...")
    init_db()
    print()
    
    # Run migrations
    total_migrated = 0
    total_migrated += migrate_workouts()
    print()
    total_migrated += migrate_themes()
    print()
    total_migrated += migrate_usage()
    print()
    total_migrated += migrate_feedback()
    print()
    
    print("=" * 60)
    print(f"Migration complete! Migrated {total_migrated} total items")
    print("=" * 60)

if __name__ == '__main__':
    main()

