#!/usr/bin/env python3
"""
Migration script to assign existing workouts to a specific user account
Run this to migrate your existing workout history to your user account
"""

import os
import sys
import json
import getpass
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from database import get_db_connection, init_db, check_db_connection, get_cursor, is_sqlite, get_db_url
from werkzeug.security import generate_password_hash, check_password_hash

# Import parse_workout_entries from app.py
import sys
sys.path.insert(0, str(Path(__file__).parent))
from app import parse_workout_entries

load_dotenv()

BASE_DIR = Path(__file__).parent
WORKOUT_LOG = BASE_DIR / "workouts.md"
THEMES_LOG = BASE_DIR / "themes.json"
USAGE_LOG = BASE_DIR / "usage.json"
FEEDBACK_LOG = BASE_DIR / "feedback.json"

def get_user_id(username, password):
    """Get or create user and return user_id"""
    db_url = get_db_url()
    use_sqlite = is_sqlite(db_url)
    
    with get_db_connection() as conn:
        cur = get_cursor(conn)
        
        # Check if user exists
        if use_sqlite:
            cur.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,))
        else:
            cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
        result = cur.fetchone()
        
        if result:
            # User exists - verify password
            if check_password_hash(result[1], password):
                print(f"✓ Found existing user: {username}")
                return result[0]
            else:
                print(f"❌ Password incorrect for user: {username}")
                return None
        else:
            # Create new user
            print(f"Creating new user: {username}")
            password_hash = generate_password_hash(password)
            if use_sqlite:
                cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
                user_id = cur.lastrowid
            else:
                cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id", (username, password_hash))
                user_id = cur.fetchone()[0]
            print(f"✓ Created user with ID: {user_id}")
            return user_id

def migrate_workouts_to_user(user_id):
    """Migrate workouts from workouts.md to database and assign to user"""
    if not WORKOUT_LOG.exists():
        print("No workouts.md file found, skipping workout migration")
        return 0
    
    print("\nMigrating workouts...")
    content = WORKOUT_LOG.read_text()
    if not content.strip():
        print("  workouts.md is empty, skipping")
        return 0
    
    workouts = parse_workout_entries(content)
    print(f"  Found {len(workouts)} workouts to migrate")
    
    db_url = get_db_url()
    use_sqlite = is_sqlite(db_url)
    
    migrated = 0
    with get_db_connection() as conn:
        cur = get_cursor(conn)
        for workout in workouts:
            try:
                date = workout.get('date', '')
                text = workout.get('text', '')
                
                if use_sqlite:
                    cur.execute("""
                        INSERT INTO workouts (date, text, user_id)
                        VALUES (?, ?, ?)
                    """, (date, text, user_id))
                else:
                    cur.execute("""
                        INSERT INTO workouts (date, text, user_id)
                        VALUES (%s, %s, %s)
                    """, (date, text, user_id))
                migrated += 1
            except Exception as e:
                print(f"  Error migrating workout {workout.get('date', 'unknown')}: {e}")
    
    print(f"  ✓ Migrated {migrated} workouts to user")
    return migrated

def migrate_themes_to_user(user_id):
    """Migrate themes from themes.json to database and assign to user"""
    if not THEMES_LOG.exists():
        print("\nNo themes.json file found, skipping theme migration")
        return 0
    
    print("\nMigrating themes...")
    try:
        themes = json.loads(THEMES_LOG.read_text())
    except:
        print("  Error reading themes.json, skipping")
        return 0
    
    print(f"  Found {len(themes)} themes to migrate")
    
    db_url = get_db_url()
    use_sqlite = is_sqlite(db_url)
    
    migrated = 0
    with get_db_connection() as conn:
        cur = get_cursor(conn)
        for workout_key, theme in themes.items():
            try:
                if use_sqlite:
                    cur.execute("""
                        INSERT INTO themes (workout_key, theme, user_id)
                        VALUES (?, ?, ?)
                        ON CONFLICT (workout_key, user_id) DO UPDATE SET theme = ?
                    """, (workout_key, theme, user_id, theme))
                else:
                    cur.execute("""
                        INSERT INTO themes (workout_key, theme, user_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (workout_key, user_id) DO UPDATE SET theme = %s
                    """, (workout_key, theme, user_id, theme))
                migrated += 1
            except Exception as e:
                print(f"  Error migrating theme {workout_key}: {e}")
    
    print(f"  ✓ Migrated {migrated} themes to user")
    return migrated

def migrate_usage_to_user(user_id):
    """Migrate usage statistics from usage.json to database and assign to user"""
    if not USAGE_LOG.exists():
        print("\nNo usage.json file found, skipping usage migration")
        return 0
    
    print("\nMigrating usage statistics...")
    try:
        usage = json.loads(USAGE_LOG.read_text())
    except:
        print("  Error reading usage.json, skipping")
        return 0
    
    daily = usage.get('daily', {})
    print(f"  Found {len(daily)} days of usage data")
    
    db_url = get_db_url()
    use_sqlite = is_sqlite(db_url)
    
    migrated = 0
    with get_db_connection() as conn:
        cur = get_cursor(conn)
        for date_str, data in daily.items():
            try:
                # Parse date string
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                
                if use_sqlite:
                    cur.execute("""
                        INSERT INTO usage (date, input_tokens, output_tokens, cost, requests, user_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (user_id, date) DO NOTHING
                    """, (
                        date_obj,
                        data.get('input_tokens', 0),
                        data.get('output_tokens', 0),
                        data.get('cost', 0.0),
                        data.get('requests', 0),
                        user_id
                    ))
                else:
                    cur.execute("""
                        INSERT INTO usage (date, input_tokens, output_tokens, cost, requests, user_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, date) DO NOTHING
                    """, (
                        date_obj,
                        data.get('input_tokens', 0),
                        data.get('output_tokens', 0),
                        data.get('cost', 0.0),
                        data.get('requests', 0),
                        user_id
                    ))
                if cur.rowcount > 0:
                    migrated += 1
            except Exception as e:
                print(f"  Error migrating usage for {date_str}: {e}")
    
    print(f"  ✓ Migrated {migrated} days of usage data to user")
    return migrated

def main():
    """Run migration for a specific user"""
    print("=" * 60)
    print("Workout History Migration Script")
    print("=" * 60)
    print()
    print("This script will assign your existing workout history to a user account.")
    print()
    
    # Check database connection
    if not check_db_connection():
        print("❌ Database connection failed!")
        print("Make sure DATABASE_URL or POSTGRES_URL is set in your environment")
        print("Or the app will use SQLite automatically")
        return
    
    print("✓ Database connection successful")
    print()
    
    # Initialize database tables
    print("Initializing database tables...")
    init_db()
    print()
    
    # Get username and password
    if len(sys.argv) >= 3:
        username = sys.argv[1]
        password = sys.argv[2]
    else:
        username = input("Enter your username: ").strip()
        if not username:
            print("❌ Username is required")
            return
        password = getpass.getpass("Enter your password: ")
        if not password:
            print("❌ Password is required")
            return
    
    print()
    
    # Get or create user
    user_id = get_user_id(username, password)
    if not user_id:
        print("❌ Failed to authenticate or create user")
        return
    
    print()
    
    # Run migrations
    total_migrated = 0
    total_migrated += migrate_workouts_to_user(user_id)
    total_migrated += migrate_themes_to_user(user_id)
    total_migrated += migrate_usage_to_user(user_id)
    
    print()
    print("=" * 60)
    print(f"Migration complete! Migrated {total_migrated} total items to user '{username}'")
    print("=" * 60)
    print()
    print(f"You can now log in with:")
    print(f"  Username: {username}")
    print(f"  Password: {password}")
    print()

if __name__ == '__main__':
    main()

