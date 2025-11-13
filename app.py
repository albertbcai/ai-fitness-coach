#!/usr/bin/env python3
"""
AI Fitness Coach - MVP
A lightweight, notes-app-style fitness coach
"""

import os
import json
import re
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from anthropic import Anthropic
from dotenv import load_dotenv
from database import get_db_connection, init_db, check_db_connection, get_cursor, is_sqlite, get_db_url
from functools import wraps

load_dotenv()

app = Flask(__name__)
# Detect production environment (Railway sets DATABASE_URL, or we check for Railway env vars)
# Also check if SECRET_KEY is explicitly set (indicates production setup)
is_production_env = (
    os.getenv('DATABASE_URL') is not None and 'postgres' in os.getenv('DATABASE_URL', '').lower()
) or os.getenv('RAILWAY_ENVIRONMENT') is not None or os.getenv('RAILWAY') is not None or os.getenv('SECRET_KEY') is not None

# Trust Railway's proxy headers for HTTPS detection
if is_production_env:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
# Set a secret key for sessions (use environment variable or generate one)
# IMPORTANT: Set SECRET_KEY in Railway environment variables for session persistence
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
# Make sessions permanent (persist until logout)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)  # Sessions last 1 year
# Configure session cookies for production
# Railway uses HTTPS, so set secure cookies when not in local development
is_production = is_production_env or os.getenv('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_SECURE'] = is_production
app.config['SESSION_COOKIE_HTTPONLY'] = True
# Use Lax for same-site cookie policy (works with Railway's proxy)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# For Railway, ensure cookies work behind proxy
if is_production:
    app.config['SESSION_COOKIE_DOMAIN'] = None  # Let Railway handle domain
    # Ensure cookie path is root
    app.config['SESSION_COOKIE_PATH'] = '/'

# Initialize database on startup
try:
    if check_db_connection():
        init_db()
        print("✓ Database initialized")
        USE_DATABASE = True
    else:
        print("⚠ Database not available, falling back to file storage")
        USE_DATABASE = False
except Exception as e:
    print(f"⚠ Database initialization failed: {e}")
    print("⚠ Falling back to file storage")
    USE_DATABASE = False

# Initialize Claude client
anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Paths
BASE_DIR = Path(__file__).parent
WORKOUT_LOG = BASE_DIR / "workouts.md"
KNOWLEDGE_DIR = Path(__file__).parent.parent / "Knowledge"
USAGE_LOG = BASE_DIR / "usage.json"
THEMES_LOG = BASE_DIR / "themes.json"
KNOWLEDGE_BASE = BASE_DIR / "knowledge_base.json"
FEEDBACK_LOG = BASE_DIR / "feedback.json"
SEARCH_INDEX = BASE_DIR / "search_index.json"

# Claude 3.5 Sonnet pricing (per 1M tokens)
INPUT_COST_PER_MILLION = 3.00  # $3 per million input tokens
OUTPUT_COST_PER_MILLION = 15.00  # $15 per million output tokens

# Cost limits (configurable in .env)
DAILY_BUDGET = float(os.getenv("DAILY_BUDGET", "1.00"))  # $1/day default
MONTHLY_BUDGET = float(os.getenv("MONTHLY_BUDGET", "20.00"))  # $20/month default

# ============================================================================
# Authentication Helper Functions
# ============================================================================

def get_current_user_id():
    """Get current user ID from session - validates it's an integer for security"""
    user_id = session.get('user_id')
    if user_id is not None:
        try:
            # Ensure user_id is an integer to prevent injection
            return int(user_id)
        except (ValueError, TypeError):
            return None
    return None

def require_auth(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not get_current_user_id():
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def create_user(username, password):
    """Create a new user - validates input to prevent SQL injection"""
    if not USE_DATABASE:
        return None
    
    # Validate and sanitize username
    if not username or not isinstance(username, str):
        return None
    username = username.strip()
    if len(username) < 3 or len(username) > 50:
        return None
    # Only allow alphanumeric, underscore, and hyphen
    if not username.replace('_', '').replace('-', '').isalnum():
        return None
    
    # Validate password
    if not password or not isinstance(password, str) or len(password) < 6:
        return None
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            # Check if username already exists - using parameterized query
            if use_sqlite:
                cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            else:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                return None
            
            # Create new user
            password_hash = generate_password_hash(password)
            if use_sqlite:
                cur.execute("""
                    INSERT INTO users (username, password_hash) 
                    VALUES (?, ?)
                """, (username, password_hash))
                # For SQLite, get lastrowid from the cursor wrapper
                user_id = cur.lastrowid
            else:
                cur.execute("""
                    INSERT INTO users (username, password_hash) 
                    VALUES (%s, %s) 
                    RETURNING id
                """, (username, password_hash))
                user_id = cur.fetchone()[0]
            return user_id
    except Exception as e:
        print(f"Error creating user: {e}")
        return None

def authenticate_user(username, password):
    """Authenticate a user and return user_id if successful - validates input to prevent SQL injection"""
    if not USE_DATABASE:
        return None
    
    # Validate username
    if not username or not isinstance(username, str):
        return None
    username = username.strip()
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            # Get user by username - using parameterized query (SQL injection protected)
            if use_sqlite:
                cur.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,))
            else:
                cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            result = cur.fetchone()
            if result and check_password_hash(result[1], password):
                return result[0]
            return None
    except Exception as e:
        print(f"Error authenticating user: {e}")
        return None

# ============================================================================
# Database Helper Functions
# ============================================================================

def get_workouts_from_db(user_id=None, limit=None):
    """Get all workouts from database, ordered by date descending - ALWAYS filters by user_id for security"""
    if not USE_DATABASE:
        return None
    
    if not user_id:
        user_id = get_current_user_id()
    
    # Security: Require user_id - never return workouts without user filter
    # Validate user_id is an integer to prevent SQL injection
    if not user_id:
        return []
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return []
    
    # Validate limit if provided
    if limit is not None:
        try:
            limit = int(limit)
            if limit < 1 or limit > 1000:  # Reasonable bounds
                limit = 100
        except (ValueError, TypeError):
            limit = None
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            # Always filter by user_id - SQL injection protection via parameterized queries
            if limit:
                if use_sqlite:
                    cur.execute("""
                        SELECT date, text 
                        FROM workouts 
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (user_id, limit))
                else:
                    cur.execute("""
                        SELECT date, text 
                        FROM workouts 
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """, (user_id, limit))
            else:
                if use_sqlite:
                    cur.execute("""
                        SELECT date, text 
                        FROM workouts 
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                    """, (user_id,))
                else:
                    cur.execute("""
                        SELECT date, text 
                        FROM workouts 
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                    """, (user_id,))
            workouts = []
            for row in cur.fetchall():
                workouts.append({
                    'date': row[0],
                    'text': row[1]
                })
            return workouts
    except Exception as e:
        print(f"Error getting workouts from database: {e}")
        return []

def add_workout_to_db(date, text, user_id=None):
    """Add a workout to the database"""
    if not USE_DATABASE:
        return False
    
    if not user_id:
        user_id = get_current_user_id()
    
    if not user_id:
        return False
    
    # Validate user_id is an integer
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return False
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            if use_sqlite:
                cur.execute("""
                    INSERT INTO workouts (date, text, user_id) 
                    VALUES (?, ?, ?)
                """, (date, text, user_id))
                workout_id = cur.lastrowid
            else:
                cur.execute("""
                    INSERT INTO workouts (date, text, user_id) 
                    VALUES (%s, %s, %s) 
                    RETURNING id
                """, (date, text, user_id))
                workout_id = cur.fetchone()[0]
            return workout_id
    except Exception as e:
        print(f"Error adding workout to database: {e}")
        import traceback
        traceback.print_exc()
        return False

def update_workout_in_db(old_date, old_text, new_text, user_id=None):
    """Update a workout in the database"""
    if not USE_DATABASE:
        return False
    
    if not user_id:
        user_id = get_current_user_id()
    
    if not user_id:
        return False
    
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return False
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            if use_sqlite:
                cur.execute("""
                    UPDATE workouts 
                    SET text = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE date = ? AND text = ? AND user_id = ?
                """, (new_text, old_date, old_text, user_id))
            else:
                cur.execute("""
                    UPDATE workouts 
                    SET text = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE date = %s AND text = %s AND user_id = %s
                """, (new_text, old_date, old_text, user_id))
            return cur.rowcount > 0
    except Exception as e:
        print(f"Error updating workout in database: {e}")
        return False

def delete_workout_from_db(date, text, user_id=None):
    """Delete a workout from the database"""
    if not USE_DATABASE:
        return False
    
    if not user_id:
        user_id = get_current_user_id()
    
    if not user_id:
        return False
    
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return False
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            if use_sqlite:
                cur.execute("""
                    DELETE FROM workouts 
                    WHERE date = ? AND text = ? AND user_id = ?
                """, (date, text, user_id))
            else:
                cur.execute("""
                    DELETE FROM workouts 
                    WHERE date = %s AND text = %s AND user_id = %s
                """, (date, text, user_id))
            return cur.rowcount > 0
    except Exception as e:
        print(f"Error deleting workout from database: {e}")
        return False

def get_theme_from_db(workout_key, user_id=None):
    """Get theme from database"""
    if not USE_DATABASE:
        return None
    
    if not user_id:
        user_id = get_current_user_id()
    
    if not user_id:
        return None
    
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return None
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            if use_sqlite:
                cur.execute("SELECT theme FROM themes WHERE workout_key = ? AND user_id = ?", (workout_key, user_id))
            else:
                cur.execute("SELECT theme FROM themes WHERE workout_key = %s AND user_id = %s", (workout_key, user_id))
            result = cur.fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"Error getting theme from database: {e}")
        return None

def save_theme_to_db(workout_key, theme, user_id=None):
    """Save theme to database"""
    if not USE_DATABASE:
        return False
    
    if not user_id:
        user_id = get_current_user_id()
    
    if not user_id:
        return False
    
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return False
    
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            cur = get_cursor(conn)
            if use_sqlite:
                # SQLite uses INSERT OR REPLACE or check if exists first
                cur.execute("""
                    INSERT OR REPLACE INTO themes (workout_key, theme, user_id, updated_at) 
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (workout_key, theme, user_id))
            else:
                cur.execute("""
                    INSERT INTO themes (workout_key, theme, user_id) 
                    VALUES (%s, %s, %s)
                    ON CONFLICT (workout_key, user_id) 
                    DO UPDATE SET theme = %s, updated_at = CURRENT_TIMESTAMP
                """, (workout_key, theme, user_id, theme))
            return True
    except Exception as e:
        print(f"Error saving theme to database: {e}")
        import traceback
        traceback.print_exc()
        return False

def load_usage(user_id=None):
    """Load usage statistics from database or file"""
    # Don't try to get user_id from session if called outside request context
    try:
        if not user_id:
            user_id = get_current_user_id()
    except RuntimeError:
        # Called outside request context (e.g., at startup)
        user_id = None
    
    if USE_DATABASE:
        try:
            db_url = get_db_url()
            use_sqlite = is_sqlite(db_url)
            with get_db_connection() as conn:
                cur = get_cursor(conn)
                # Get daily usage
                if user_id:
                    try:
                        user_id = int(user_id)
                    except (ValueError, TypeError):
                        user_id = None
                    
                    if user_id:
                        if use_sqlite:
                            cur.execute("""
                                SELECT date, input_tokens, output_tokens, cost, requests
                                FROM usage
                                WHERE user_id = ?
                                ORDER BY date DESC
                            """, (user_id,))
                        else:
                            cur.execute("""
                                SELECT date, input_tokens, output_tokens, cost, requests
                                FROM usage
                                WHERE user_id = %s
                                ORDER BY date DESC
                            """, (user_id,))
                    else:
                        if use_sqlite:
                            cur.execute("""
                                SELECT date, input_tokens, output_tokens, cost, requests
                                FROM usage
                                ORDER BY date DESC
                            """)
                        else:
                            cur.execute("""
                                SELECT date, input_tokens, output_tokens, cost, requests
                                FROM usage
                                ORDER BY date DESC
                            """)
                else:
                    if use_sqlite:
                        cur.execute("""
                            SELECT date, input_tokens, output_tokens, cost, requests
                            FROM usage
                            ORDER BY date DESC
                        """)
                    else:
                        cur.execute("""
                            SELECT date, input_tokens, output_tokens, cost, requests
                            FROM usage
                            ORDER BY date DESC
                        """)
                daily = {}
                total_input = 0
                total_output = 0
                total_cost = 0.0
                
                for row in cur.fetchall():
                    date_str = row[0].strftime("%Y-%m-%d") if hasattr(row[0], 'strftime') else str(row[0])
                    daily[date_str] = {
                        "input_tokens": row[1],
                        "output_tokens": row[2],
                        "cost": float(row[3]),
                        "requests": row[4]
                    }
                    total_input += row[1]
                    total_output += row[2]
                    total_cost += float(row[3])
                
                return {
                    "daily": daily,
                    "total": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "cost": total_cost
                    }
                }
        except Exception as e:
            print(f"Error loading usage from database: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to file-based
    
    # File-based fallback
    if USAGE_LOG.exists():
        try:
            return json.loads(USAGE_LOG.read_text())
        except:
            return {"daily": {}, "total": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}}
    return {"daily": {}, "total": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}}

def load_search_index():
    """Load the search index for preset searches"""
    if SEARCH_INDEX.exists():
        try:
            return json.loads(SEARCH_INDEX.read_text())
        except:
            return {}
    return {}

def save_search_index(index):
    """Save the search index"""
    SEARCH_INDEX.write_text(json.dumps(index, indent=2))

def get_workout_hash():
    """Get a hash of the workout file to detect changes"""
    import hashlib
    if WORKOUT_LOG.exists():
        content = WORKOUT_LOG.read_text()
        return hashlib.md5(content.encode()).hexdigest()
    return ""

def build_search_index():
    """Build search index using AI - maps preset queries to workout indices"""
    # Load workouts
    workouts = []
    if WORKOUT_LOG.exists():
        content = WORKOUT_LOG.read_text()
        if content.strip():
            workouts.extend(parse_workout_entries(content))
    
    if not workouts:
        return {}
    
    # Load themes and detect PRs (same as search_workouts)
    themes = load_themes()
    from workout_parser import parse_workout_text, extract_muscle_groups_from_exercises
    from datetime import datetime
    today = datetime.now()
    
    # Detect PRs for workouts
    for i, workout in enumerate(workouts):
        workout_key = get_workout_key(workout.get('date', ''), workout.get('text', ''))
        workout['theme'] = themes.get(workout_key, None)
        
        has_pr = False
        has_strength_increase = False
        
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if workout_date:
            history_before = {}
            for prev_workout in workouts:
                prev_date_str = prev_workout.get('date', '')
                prev_date = None
                if prev_date_str:
                    for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                        try:
                            prev_parsed = datetime.strptime(prev_date_str, fmt)
                            if prev_parsed.year > today.year + 1 or (prev_parsed - today).days > 1:
                                continue
                            prev_date = prev_parsed
                            break
                        except:
                            continue
                
                if prev_date and prev_date < workout_date:
                    prev_parsed_exercises = parse_workout_text(prev_workout.get('text', '')).get('exercises', [])
                    for ex in prev_parsed_exercises:
                        ex_key = ex['exercise'].lower().strip()
                        max_weight = ex.get('max_weight', 0)
                        first_reps = ex.get('first_reps', 0)
                        
                        if ex_key not in history_before:
                            history_before[ex_key] = {
                                'best_weight': max_weight,
                                'best_reps': first_reps,
                                'best_weight_reps': first_reps if max_weight > 0 else 0
                            }
                        else:
                            if max_weight > history_before[ex_key]['best_weight']:
                                history_before[ex_key]['best_weight'] = max_weight
                                history_before[ex_key]['best_weight_reps'] = first_reps
                            if first_reps > history_before[ex_key]['best_reps']:
                                history_before[ex_key]['best_reps'] = first_reps
            
            current_parsed_exercises = parse_workout_text(workout.get('text', '')).get('exercises', [])
            for ex in current_parsed_exercises:
                ex_key = ex['exercise'].lower().strip()
                current_weight = ex.get('max_weight', 0)
                current_reps = ex.get('first_reps', 0)
                is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                
                if ex_key in history_before:
                    hist = history_before[ex_key]
                    if is_bodyweight:
                        if current_reps > hist['best_reps']:
                            has_pr = True
                    else:
                        if current_weight > hist['best_weight']:
                            has_pr = True
                        elif current_weight == hist['best_weight'] and current_reps > hist['best_weight_reps']:
                            has_strength_increase = True
        
        workout['has_pr'] = has_pr
        workout['has_strength_increase'] = has_strength_increase
    
    # Use rule-based logic for PR, Full Body, and Chest (more accurate)
    # Then use AI for Legs and Upper Body
    index = {}
    
    # 1. PR personal record - rule-based (use has_pr flag)
    pr_indices = []
    for i, workout in enumerate(workouts):
        if workout.get('has_pr', False):
            pr_indices.append(i)
    index['PR personal record'] = pr_indices[:20]  # Limit to 20
    
    # 2. Full Body - rule-based (3+ muscle groups)
    from workout_parser import load_exercise_mapping, normalize_exercise_name
    exercise_mapping = load_exercise_mapping()
    full_body_indices = []
    for i, workout in enumerate(workouts):
        parsed = parse_workout_text(workout.get('text', ''))
        exercises = parsed.get('exercises', [])
        if not exercises:
            continue
        
        # Get unique muscle groups
        muscle_groups = set()
        for ex in exercises:
            ex_name = ex['exercise']
            normalized_name, mapped_groups = normalize_exercise_name(ex_name)
            muscle_groups.update(mapped_groups)
        
        # Full body = 3+ distinct muscle groups
        if len(muscle_groups) >= 3:
            full_body_indices.append(i)
    index['full body'] = full_body_indices[:20]
    
    # 3. Chest workout - rule-based (use exercise mapping)
    chest_indices = []
    for i, workout in enumerate(workouts):
        parsed = parse_workout_text(workout.get('text', ''))
        exercises = parsed.get('exercises', [])
        for ex in exercises:
            ex_name = ex['exercise']
            normalized_name, mapped_groups = normalize_exercise_name(ex_name)
            if 'chest' in mapped_groups:
                chest_indices.append(i)
                break  # Only add once per workout
    index['chest workout'] = chest_indices[:20]
    
    # 4. Leg day and Upper body - use AI (more nuanced)
    workout_context = []
    for i, workout in enumerate(workouts[:100]):
        workout_text = workout.get('text', '')[:200]
        theme = workout.get('theme', '')
        date = workout.get('date', '')
        pr_flag = "🏆 PR" if workout.get('has_pr', False) else ""
        strength_flag = "📈 Strength" if workout.get('has_strength_increase', False) else ""
        flags = f" {pr_flag} {strength_flag}".strip()
        workout_context.append(f"[{i}] {date} | {theme}{flags} | {workout_text}")
    
    context_text = '\n'.join(workout_context)
    
    prompt = f"""Analyze this workout history and find workouts matching these queries.

Workout history (format: [index] date | theme | workout text):
{context_text}

For each query below, return ONLY the workout indices (numbers in brackets) that match:
1. "leg day" → workouts focused on leg exercises (squats, lunges, leg press, etc.)
2. "upper body" → workouts focused on upper body (chest, back, shoulders, arms)

Return your answer in this EXACT format (one line per query):
leg day: 1, 5, 9
upper body: 2, 4, 8

Return at most 20 indices per query, prioritizing most relevant matches. Be precise - only include workouts that clearly match the category."""

    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        result_text = message.content[0].text.strip()
        
        # Parse the results
        import re
        # Parse leg day
        leg_match = re.search(r'leg day[^:]*:\s*([0-9,\s]+)', result_text, re.IGNORECASE)
        if leg_match:
            indices_str = leg_match.group(1)
            indices = [int(idx.strip()) for idx in indices_str.split(',') if idx.strip().isdigit() and int(idx.strip()) < len(workouts)]
            index['leg day'] = indices[:20]
        else:
            index['leg day'] = []
        
        # Parse upper body
        upper_match = re.search(r'upper body[^:]*:\s*([0-9,\s]+)', result_text, re.IGNORECASE)
        if upper_match:
            indices_str = upper_match.group(1)
            indices = [int(idx.strip()) for idx in indices_str.split(',') if idx.strip().isdigit() and int(idx.strip()) < len(workouts)]
            index['upper body'] = indices[:20]
        else:
            index['upper body'] = []
        
    except Exception as e:
        print(f"Error building AI search index: {e}")
        # Fallback to empty for AI-based queries
        index['leg day'] = []
        index['upper body'] = []
    
    # Add metadata
    index['_metadata'] = {
        'workout_hash': get_workout_hash(),
        'workout_count': len(workouts),
        'last_updated': datetime.now().isoformat()
    }
    
    return index

def update_index_for_workout(workout_index, workout_data, operation='add'):
    """Incrementally update index for a single workout (rule-based categories only)"""
    index = load_search_index()
    
    # Initialize if needed
    if not index or '_metadata' not in index:
        return  # Can't do incremental update without existing index
    
    # Rule-based categories that can be updated incrementally
    rule_based_categories = ['PR personal record', 'chest workout', 'full body']
    
    if operation == 'add' or operation == 'update':
        # Remove from all categories first (in case of update)
        for category in rule_based_categories:
            if category in index and workout_index in index[category]:
                index[category].remove(workout_index)
        
        # Add to appropriate categories
        if workout_data.get('has_pr', False):
            if 'PR personal record' not in index:
                index['PR personal record'] = []
            if workout_index not in index['PR personal record']:
                index['PR personal record'].append(workout_index)
        
        # Check muscle groups for chest and full body
        from workout_parser import parse_workout_text, normalize_exercise_name
        parsed = parse_workout_text(workout_data.get('text', ''))
        exercises = parsed.get('exercises', [])
        
        if exercises:
            muscle_groups = set()
            has_chest = False
            
            for ex in exercises:
                ex_name = ex['exercise']
                normalized_name, mapped_groups = normalize_exercise_name(ex_name)
                muscle_groups.update(mapped_groups)
                if 'chest' in mapped_groups:
                    has_chest = True
            
            if has_chest:
                if 'chest workout' not in index:
                    index['chest workout'] = []
                if workout_index not in index['chest workout']:
                    index['chest workout'].append(workout_index)
            
            if len(muscle_groups) >= 3:
                if 'full body' not in index:
                    index['full body'] = []
                if workout_index not in index['full body']:
                    index['full body'].append(workout_index)
    
    elif operation == 'remove':
        # Remove from all categories
        for category in rule_based_categories:
            if category in index and workout_index in index[category]:
                index[category].remove(workout_index)
    
    # Update metadata
    index['_metadata']['workout_hash'] = get_workout_hash()
    index['_metadata']['last_updated'] = datetime.now().isoformat()
    
    save_search_index(index)

def rebuild_ai_index_async():
    """Rebuild AI-based index categories in background (non-blocking)"""
    import threading
    
    def _rebuild():
        try:
            print("Rebuilding AI search index in background...")
            # Load current workouts
            workouts = []
            if WORKOUT_LOG.exists():
                content = WORKOUT_LOG.read_text()
                if content.strip():
                    workouts.extend(parse_workout_entries(content))
            
            if not workouts:
                return
            
            # Load existing index
            index = load_search_index()
            if not index:
                index = {}
            
            # Only rebuild AI-based categories (legs, upper body)
            # Keep rule-based categories as-is
            themes = load_themes()
            from workout_parser import parse_workout_text
            from datetime import datetime
            today = datetime.now()
            
            # Detect PRs for workouts (needed for context)
            for i, workout in enumerate(workouts):
                workout_key = get_workout_key(workout.get('date', ''), workout.get('text', ''))
                workout['theme'] = themes.get(workout_key, None)
                
                has_pr = False
                workout_date_str = workout.get('date', '')
                workout_date = None
                if workout_date_str:
                    for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                        try:
                            parsed_date = datetime.strptime(workout_date_str, fmt)
                            if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                                continue
                            workout_date = parsed_date
                            break
                        except:
                            continue
                
                if workout_date:
                    history_before = {}
                    for prev_workout in workouts:
                        prev_date_str = prev_workout.get('date', '')
                        prev_date = None
                        if prev_date_str:
                            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                                try:
                                    prev_parsed = datetime.strptime(prev_date_str, fmt)
                                    if prev_parsed.year > today.year + 1 or (prev_parsed - today).days > 1:
                                        continue
                                    prev_date = prev_parsed
                                    break
                                except:
                                    continue
                        
                        if prev_date and prev_date < workout_date:
                            prev_parsed_exercises = parse_workout_text(prev_workout.get('text', '')).get('exercises', [])
                            for ex in prev_parsed_exercises:
                                ex_key = ex['exercise'].lower().strip()
                                max_weight = ex.get('max_weight', 0)
                                first_reps = ex.get('first_reps', 0)
                                
                                if ex_key not in history_before:
                                    history_before[ex_key] = {
                                        'best_weight': max_weight,
                                        'best_reps': first_reps,
                                        'best_weight_reps': first_reps if max_weight > 0 else 0
                                    }
                                else:
                                    if max_weight > history_before[ex_key]['best_weight']:
                                        history_before[ex_key]['best_weight'] = max_weight
                                        history_before[ex_key]['best_weight_reps'] = first_reps
                                    if first_reps > history_before[ex_key]['best_reps']:
                                        history_before[ex_key]['best_reps'] = first_reps
                    
                    current_parsed_exercises = parse_workout_text(workout.get('text', '')).get('exercises', [])
                    for ex in current_parsed_exercises:
                        ex_key = ex['exercise'].lower().strip()
                        current_weight = ex.get('max_weight', 0)
                        current_reps = ex.get('first_reps', 0)
                        is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                        
                        if ex_key in history_before:
                            hist = history_before[ex_key]
                            if is_bodyweight:
                                if current_reps > hist['best_reps']:
                                    has_pr = True
                            else:
                                if current_weight > hist['best_weight']:
                                    has_pr = True
                
                workout['has_pr'] = has_pr
            
            # Build context for AI
            workout_context = []
            for i, workout in enumerate(workouts[:100]):
                workout_text = workout.get('text', '')[:200]
                theme = workout.get('theme', '')
                date = workout.get('date', '')
                pr_flag = "🏆 PR" if workout.get('has_pr', False) else ""
                flags = f" {pr_flag}".strip()
                workout_context.append(f"[{i}] {date} | {theme}{flags} | {workout_text}")
            
            context_text = '\n'.join(workout_context)
            
            prompt = f"""Analyze this workout history and find workouts matching these queries.

Workout history (format: [index] date | theme | workout text):
{context_text}

For each query below, return ONLY the workout indices (numbers in brackets) that match:
1. "leg day" → workouts focused on leg exercises (squats, lunges, leg press, etc.)
2. "upper body" → workouts focused on upper body (chest, back, shoulders, arms)

Return your answer in this EXACT format (one line per query):
leg day: 1, 5, 9
upper body: 2, 4, 8

Return at most 20 indices per query, prioritizing most relevant matches. Be precise - only include workouts that clearly match the category."""

            message = anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Track usage
            input_tokens = message.usage.input_tokens
            output_tokens = message.usage.output_tokens
            update_usage(input_tokens, output_tokens)
            
            result_text = message.content[0].text.strip()
            
            # Parse the results
            import re
            # Parse leg day
            leg_match = re.search(r'leg day[^:]*:\s*([0-9,\s]+)', result_text, re.IGNORECASE)
            if leg_match:
                indices_str = leg_match.group(1)
                indices = [int(idx.strip()) for idx in indices_str.split(',') if idx.strip().isdigit() and int(idx.strip()) < len(workouts)]
                index['leg day'] = indices[:20]
            else:
                index['leg day'] = []
            
            # Parse upper body
            upper_match = re.search(r'upper body[^:]*:\s*([0-9,\s]+)', result_text, re.IGNORECASE)
            if upper_match:
                indices_str = upper_match.group(1)
                indices = [int(idx.strip()) for idx in indices_str.split(',') if idx.strip().isdigit() and int(idx.strip()) < len(workouts)]
                index['upper body'] = indices[:20]
            else:
                index['upper body'] = []
            
            # Update metadata
            index['_metadata']['workout_hash'] = get_workout_hash()
            index['_metadata']['workout_count'] = len(workouts)
            index['_metadata']['last_updated'] = datetime.now().isoformat()
            
            save_search_index(index)
            print("AI search index rebuilt in background")
        except Exception as e:
            print(f"Error rebuilding AI index: {e}")
    
    thread = threading.Thread(target=_rebuild)
    thread.daemon = True
    thread.start()
    return thread

def ensure_search_index():
    """Ensure search index exists and is up to date"""
    current_hash = get_workout_hash()
    index = load_search_index()
    
    # Check if index needs updating
    needs_update = False
    if not index or '_metadata' not in index:
        needs_update = True
    elif index['_metadata'].get('workout_hash') != current_hash:
        needs_update = True
    
    if needs_update:
        # If index doesn't exist, do full build
        if not index or '_metadata' not in index:
            print("Building search index...")
            index = build_search_index()
            if index:
                save_search_index(index)
                print("Search index built")
        else:
            # Index exists but is stale - trigger background AI rebuild
            # Rule-based categories will be updated incrementally when workouts change
            rebuild_ai_index_async()
            # Return existing index (may be slightly stale for AI categories)
    
    return index

def load_knowledge_base():
    """Load science-backed knowledge base"""
    if KNOWLEDGE_BASE.exists():
        try:
            return json.loads(KNOWLEDGE_BASE.read_text())
        except:
            return {}
    return {}

def get_knowledge_summary(knowledge_base, emphasize_user_data=True):
    """Extract key principles from knowledge base for prompts, weighted by confidence"""
    if not knowledge_base:
        return ""
    
    high_confidence = []  # Strong principles (less flexible)
    medium_confidence = []  # Guidelines (more flexible, emphasize user data)
    
    # Recovery times (medium confidence, high flexibility - emphasize user data)
    if "recovery" in knowledge_base:
        recovery = knowledge_base["recovery"]
        if "muscle_group_recovery" in recovery:
            rec = recovery["muscle_group_recovery"]
            confidence = rec.get('confidence', 'medium')
            emphasis = rec.get('emphasis', 'user_data')
            note = f" (guideline - individual variation is huge, prioritize user's actual recovery patterns)"
            if confidence == 'high' and emphasis == 'principle':
                high_confidence.append(f"Recovery: Muscle groups typically need {rec.get('minimum_hours', 48)}-{rec.get('maximum_hours', 72)} hours between sessions")
            else:
                medium_confidence.append(f"Recovery: {rec.get('minimum_hours', 48)}-{rec.get('maximum_hours', 72)} hours is a guideline{note if emphasize_user_data else ''}")
    
    # Progressive overload (high confidence, medium flexibility - principle-based)
    if "progressive_overload" in knowledge_base:
        po = knowledge_base["progressive_overload"]
        if "weight_increase" in po:
            wi = po["weight_increase"]
            confidence = wi.get('confidence', 'high')
            emphasis = wi.get('emphasis', 'principle')
            if confidence == 'high':
                high_confidence.append(f"Progressive Overload: Gradually increase weight/volume over time (guideline: {wi.get('intermediate_weekly', '2.5-5%')} weekly, but individual variation is huge)")
            else:
                medium_confidence.append(f"Progressive Overload: {wi.get('intermediate_weekly', '2.5-5%')} weekly is a guideline - prioritize user's actual progression patterns")
    
    # Volume (medium confidence, high flexibility - emphasize user data)
    if "volume" in knowledge_base:
        vol = knowledge_base["volume"]
        if "sets_per_muscle_group" in vol:
            sets = vol["sets_per_muscle_group"]
            confidence = sets.get('confidence', 'medium')
            emphasis = sets.get('emphasis', 'user_data')
            note = " (guideline - adjust based on user's actual volume and recovery)"
            if confidence == 'high':
                high_confidence.append(f"Volume: Aim for {sets.get('intermediate_per_week', '15-20')} sets per muscle group per week")
            else:
                medium_confidence.append(f"Volume: {sets.get('intermediate_per_week', '15-20')} sets per week is a guideline{note if emphasize_user_data else ''}")
        if "reps_per_set" in vol:
            reps = vol["reps_per_set"]
            high_confidence.append(f"Reps: {reps.get('hypertrophy', '6-12 reps')} for hypertrophy, {reps.get('strength', '1-5 reps')} for strength")
    
    # Frequency (medium confidence, high flexibility - emphasize user data)
    if "frequency" in knowledge_base:
        freq = knowledge_base["frequency"]
        if "per_muscle_group" in freq:
            f = freq["per_muscle_group"]
            confidence = f.get('confidence', 'medium')
            emphasis = f.get('emphasis', 'user_data')
            note = " (guideline - prioritize user's actual training frequency patterns)"
            if confidence == 'high':
                high_confidence.append(f"Frequency: Train each muscle group {f.get('optimal', '2-3 times per week')}")
            else:
                medium_confidence.append(f"Frequency: {f.get('optimal', '2-3 times per week')} is a guideline{note if emphasize_user_data else ''}")
    
    # Exercise selection (high confidence, low flexibility - strong principle)
    if "exercise_selection" in knowledge_base:
        es = knowledge_base["exercise_selection"]
        if "compound_movements" in es:
            cm = es["compound_movements"]
            confidence = cm.get('confidence', 'high')
            if confidence == 'high':
                high_confidence.append("Exercise Selection: Prioritize compound movements (squat, deadlift, bench, row, overhead press, pull-up)")
    
    # Muscle groups (for reference)
    if "muscle_groups" in knowledge_base and "categorization" in knowledge_base["muscle_groups"]:
        mg = knowledge_base["muscle_groups"]["categorization"]
        muscle_list = []
        for group, info in mg.items():
            if isinstance(info, dict) and "primary_exercises" in info:
                exercises = ", ".join(info["primary_exercises"][:3])
                muscle_list.append(f"{group}: {exercises}")
        if muscle_list:
            high_confidence.append(f"Muscle Groups: {', '.join(muscle_list[:5])}")
    
    # Build summary with confidence weighting
    summary = []
    if high_confidence:
        summary.append("Strong Principles (apply these):")
        summary.extend(high_confidence)
    if medium_confidence:
        summary.append("\nGuidelines (use as reference, but prioritize user's actual patterns):")
        summary.extend(medium_confidence)
    
    return "\n".join(summary)

def load_themes(user_id=None):
    """Load saved themes from database or file"""
    if not user_id:
        user_id = get_current_user_id()
    
    if USE_DATABASE:
        try:
            db_url = get_db_url()
            use_sqlite = is_sqlite(db_url)
            with get_db_connection() as conn:
                cur = get_cursor(conn)
                if user_id:
                    if use_sqlite:
                        cur.execute("SELECT workout_key, theme FROM themes WHERE user_id = ?", (user_id,))
                    else:
                        cur.execute("SELECT workout_key, theme FROM themes WHERE user_id = %s", (user_id,))
                else:
                    cur.execute("SELECT workout_key, theme FROM themes")
                themes = {}
                for row in cur.fetchall():
                    themes[row[0]] = row[1]
                return themes
        except Exception as e:
            print(f"Error loading themes from database: {e}")
            # Fall through to file-based
    
    # File-based fallback (only if no database or not authenticated)
    if THEMES_LOG.exists():
        try:
            return json.loads(THEMES_LOG.read_text())
        except:
            return {}
    return {}

def save_themes(themes):
    """Save themes to database or file"""
    if USE_DATABASE:
        try:
            user_id = get_current_user_id()
            if not user_id:
                # Can't save without user_id
                return
            
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                return
            
            db_url = get_db_url()
            use_sqlite = is_sqlite(db_url)
            with get_db_connection() as conn:
                cur = get_cursor(conn)
                for workout_key, theme in themes.items():
                    if use_sqlite:
                        cur.execute("""
                            INSERT OR REPLACE INTO themes (workout_key, theme, user_id, updated_at) 
                            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        """, (workout_key, theme, user_id))
                    else:
                        cur.execute("""
                            INSERT INTO themes (workout_key, theme, user_id) 
                            VALUES (%s, %s, %s)
                            ON CONFLICT (workout_key, user_id) 
                            DO UPDATE SET theme = %s, updated_at = CURRENT_TIMESTAMP
                        """, (workout_key, theme, user_id, theme))
            return
        except Exception as e:
            print(f"Error saving themes to database: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to file-based
    
    # File-based fallback
    THEMES_LOG.write_text(json.dumps(themes, indent=2))

def get_workout_key(date, text):
    """Generate a unique key for a workout entry"""
    import hashlib
    key = f"{date}:{text[:100]}"  # Use date + first 100 chars of text
    return hashlib.md5(key.encode()).hexdigest()

def save_usage(usage):
    """Save usage statistics"""
    USAGE_LOG.write_text(json.dumps(usage, indent=2))

def calculate_cost(input_tokens, output_tokens):
    """Calculate cost in dollars"""
    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_MILLION
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_MILLION
    return input_cost + output_cost

def update_usage(input_tokens, output_tokens, user_id=None):
    """Update usage statistics in database or file"""
    if not user_id:
        user_id = get_current_user_id()
    
    today = datetime.now().date()
    cost = calculate_cost(input_tokens, output_tokens)
    
    if USE_DATABASE and user_id:
        try:
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                # Fall through to file-based
                pass
            else:
                db_url = get_db_url()
                use_sqlite = is_sqlite(db_url)
                with get_db_connection() as conn:
                    cur = get_cursor(conn)
                    if use_sqlite:
                        # SQLite: Check if exists, then update or insert
                        cur.execute("""
                            SELECT input_tokens, output_tokens, cost, requests
                            FROM usage
                            WHERE user_id = ? AND date = ?
                        """, (user_id, today))
                        existing = cur.fetchone()
                        if existing:
                            cur.execute("""
                                UPDATE usage
                                SET input_tokens = input_tokens + ?,
                                    output_tokens = output_tokens + ?,
                                    cost = cost + ?,
                                    requests = requests + 1,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE user_id = ? AND date = ?
                            """, (input_tokens, output_tokens, cost, user_id, today))
                        else:
                            cur.execute("""
                                INSERT INTO usage (date, input_tokens, output_tokens, cost, requests, user_id)
                                VALUES (?, ?, ?, ?, 1, ?)
                            """, (today, input_tokens, output_tokens, cost, user_id))
                    else:
                        cur.execute("""
                            INSERT INTO usage (date, input_tokens, output_tokens, cost, requests, user_id)
                            VALUES (%s, %s, %s, %s, 1, %s)
                            ON CONFLICT (user_id, date) 
                            DO UPDATE SET 
                                input_tokens = usage.input_tokens + %s,
                                output_tokens = usage.output_tokens + %s,
                                cost = usage.cost + %s,
                                requests = usage.requests + 1,
                                updated_at = CURRENT_TIMESTAMP
                        """, (today, input_tokens, output_tokens, cost, user_id, input_tokens, output_tokens, cost))
                return
        except Exception as e:
            print(f"Error updating usage in database: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to file-based
    
    # File-based fallback
    usage = load_usage(user_id)
    today_str = today.strftime("%Y-%m-%d")
    
    # Update daily usage
    if today_str not in usage["daily"]:
        usage["daily"][today_str] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "requests": 0}
    
    usage["daily"][today_str]["input_tokens"] += input_tokens
    usage["daily"][today_str]["output_tokens"] += output_tokens
    usage["daily"][today_str]["cost"] += cost
    usage["daily"][today_str]["requests"] += 1
    
    # Update total usage
    usage["total"]["input_tokens"] += input_tokens
    usage["total"]["output_tokens"] += output_tokens
    usage["total"]["cost"] += cost
    
    # Clean up old daily data (keep last 30 days)
    cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    usage["daily"] = {k: v for k, v in usage["daily"].items() if k >= cutoff_date}
    
    save_usage(usage)
    return usage["daily"].get(today_str, {}).get("cost", 0.0), usage["total"]["cost"]

def check_budget(user_id=None):
    """Check if we're within budget"""
    # Don't try to get user_id from session if called outside request context
    try:
        if not user_id:
            user_id = get_current_user_id()
    except RuntimeError:
        # Called outside request context (e.g., at startup)
        user_id = None
    
    usage = load_usage(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    daily_cost = usage["daily"].get(today, {}).get("cost", 0.0)
    total_cost = usage["total"]["cost"]
    
    # Check monthly budget (rough estimate - last 30 days)
    monthly_cost = sum(day["cost"] for day in usage["daily"].values())
    
    return {
        "daily_cost": daily_cost,
        "monthly_cost": monthly_cost,
        "total_cost": total_cost,
        "daily_budget": DAILY_BUDGET,
        "monthly_budget": MONTHLY_BUDGET,
        "daily_remaining": max(0, DAILY_BUDGET - daily_cost),
        "monthly_remaining": max(0, MONTHLY_BUDGET - monthly_cost),
        "over_daily_budget": daily_cost >= DAILY_BUDGET,
        "over_monthly_budget": monthly_cost >= MONTHLY_BUDGET
    }

def load_workout_history():
    """Load workout history from markdown file"""
    if WORKOUT_LOG.exists():
        return WORKOUT_LOG.read_text()
    return ""

def load_user_context():
    """Load user context from Knowledge folder"""
    context = {}
    
    # Load workout log if exists - REDUCED to save costs (last 20k chars = ~5k tokens)
    workout_log_path = KNOWLEDGE_DIR / "workout_log.md"
    if workout_log_path.exists():
        # Only load recent history to reduce token usage
        full_text = workout_log_path.read_text()
        context["workout_history"] = full_text[-20000:]  # Last 20k chars only
    
    # Load profile if exists
    profile_path = KNOWLEDGE_DIR / "albert_cai_profile.md"
    if profile_path.exists():
        context["profile"] = profile_path.read_text()
    
    return context

@app.route('/')
def index():
    """Main app interface"""
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new user"""
    if not USE_DATABASE:
        return jsonify({'error': 'Database not available'}), 500
    
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    user_id = create_user(username, password)
    if not user_id:
        return jsonify({'error': 'Username already exists'}), 400
    
    # Automatically log in the user with permanent session
    session.permanent = True
    session['user_id'] = user_id
    session['username'] = username
    # Explicitly mark session as modified to ensure it's saved
    session.modified = True
    
    return jsonify({
        'success': True,
        'user_id': user_id,
        'username': username
    })

@app.route('/api/login', methods=['POST'])
def login():
    """Login a user"""
    if not USE_DATABASE:
        return jsonify({'error': 'Database not available'}), 500
    
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    user_id = authenticate_user(username, password)
    if not user_id:
        return jsonify({'error': 'Invalid username or password'}), 401
    
    # Set permanent session (persists until logout)
    session.permanent = True
    session['user_id'] = user_id
    session['username'] = username
    # Explicitly mark session as modified to ensure it's saved
    session.modified = True
    
    return jsonify({
        'success': True,
        'user_id': user_id,
        'username': username
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout the current user"""
    session.clear()
    return jsonify({'success': True})

@app.route('/api/current-user', methods=['GET'])
def get_current_user():
    """Get current user info"""
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'authenticated': False}), 401
    
    return jsonify({
        'authenticated': True,
        'user_id': user_id,
        'username': session.get('username', '')
    })

@app.route('/api/export-workouts', methods=['GET'])
@require_auth
def export_workouts():
    """Export all workouts for the current user as markdown"""
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Get all workouts for the user
    workouts = get_workouts_from_db(user_id) or []
    
    if not workouts:
        return jsonify({'error': 'No workouts found'}), 404
    
    # Build markdown content
    markdown_content = "# Workout History\n\n"
    
    for workout in workouts:
        date = workout.get('date', '')
        text = workout.get('text', '')
        markdown_content += f"{date}\n\n{text}\n\n---\n\n"
    
    # Return as downloadable file
    from flask import Response
    response = Response(
        markdown_content,
        mimetype='text/markdown',
        headers={
            'Content-Disposition': f'attachment; filename=workout-history-{datetime.now().strftime("%Y%m%d")}.md'
        }
    )
    return response

@app.route('/api/workouts', methods=['GET'])
def get_workouts():
    """Get all workout entries from database or file"""
    workouts = []
    
    # Try database first - always use database if available and user is authenticated
    user_id = get_current_user_id()
    if USE_DATABASE and user_id:
        # Authenticated user - only get their workouts from database
        db_workouts = get_workouts_from_db(user_id)
        if db_workouts is not None:
            workouts = db_workouts
    elif USE_DATABASE:
        # Database available but not authenticated - return empty
        workouts = []
    else:
        # No database - fall back to file-based (legacy mode)
        if WORKOUT_LOG.exists():
            content = WORKOUT_LOG.read_text()
            if content.strip():
                workouts.extend(parse_workout_entries(content))
    
    # Debug: Log if workouts are empty
    if not workouts and USE_DATABASE:
        print(f"DEBUG: No workouts found. user_id={user_id}, USE_DATABASE={USE_DATABASE}")
    
    # Load saved themes and attach to workouts
    themes = load_themes()
    
    # Detect PRs and strength increases for each workout
    from workout_parser import parse_workout_text
    from datetime import datetime
    today = datetime.now()
    
    # Build exercise history for comparison
    exercise_history = {}
    for i, workout in enumerate(workouts):
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    # Only filter out future dates or dates way in the past (more than 10 years)
                    if parsed_date.year > today.year + 1 or (today - parsed_date).days > 3650:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        # Don't skip workouts if date parsing fails - just use the date string as-is
        # if not workout_date:
        #     continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            max_weight = ex.get('max_weight', 0)
            first_reps = ex.get('first_reps', 0)
            
            # Track best performance (only from workouts BEFORE this one)
            if ex_key not in exercise_history:
                exercise_history[ex_key] = {
                    'best_weight': max_weight,
                    'best_reps': first_reps,
                    'best_weight_reps': first_reps if max_weight > 0 else 0
                }
            else:
                if max_weight > exercise_history[ex_key]['best_weight']:
                    exercise_history[ex_key]['best_weight'] = max_weight
                    exercise_history[ex_key]['best_weight_reps'] = first_reps
                if first_reps > exercise_history[ex_key]['best_reps']:
                    exercise_history[ex_key]['best_reps'] = first_reps
    
    # Now check each workout for PRs/strength increases (comparing to history BEFORE it)
    for workout in workouts:
        workout_key = get_workout_key(workout.get('date', ''), workout.get('text', ''))
        workout['theme'] = themes.get(workout_key, None)
        
        # Check for PRs/strength increases in this workout
        parsed_workout = parse_workout_text(workout.get('text', ''))
        has_pr = False
        has_strength_increase = False
        
        # Build history up to this point (workouts before this one)
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if workout_date:
            # Build history from workouts before this date
            history_before = {}
            for prev_workout in workouts:
                prev_date_str = prev_workout.get('date', '')
                prev_date = None
                if prev_date_str:
                    for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                        try:
                            prev_parsed = datetime.strptime(prev_date_str, fmt)
                            if prev_parsed.year > today.year + 1 or (prev_parsed - today).days > 1:
                                continue
                            prev_date = prev_parsed
                            break
                        except:
                            continue
                
                if prev_date and prev_date < workout_date:
                    prev_parsed = parse_workout_text(prev_workout.get('text', ''))
                    for ex in prev_parsed.get('exercises', []):
                        ex_key = ex['exercise'].lower().strip()
                        max_weight = ex.get('max_weight', 0)
                        first_reps = ex.get('first_reps', 0)
                        
                        if ex_key not in history_before:
                            history_before[ex_key] = {
                                'best_weight': max_weight,
                                'best_reps': first_reps,
                                'best_weight_reps': first_reps if max_weight > 0 else 0
                            }
                        else:
                            if max_weight > history_before[ex_key]['best_weight']:
                                history_before[ex_key]['best_weight'] = max_weight
                                history_before[ex_key]['best_weight_reps'] = first_reps
                            if first_reps > history_before[ex_key]['best_reps']:
                                history_before[ex_key]['best_reps'] = first_reps
            
            # Check current workout against history
            for ex in parsed_workout.get('exercises', []):
                ex_key = ex['exercise'].lower().strip()
                current_weight = ex.get('max_weight', 0)
                current_reps = ex.get('first_reps', 0)
                is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                
                if ex_key in history_before:
                    hist = history_before[ex_key]
                    if is_bodyweight:
                        if current_reps > hist['best_reps']:
                            has_pr = True
                    else:
                        if current_weight > hist['best_weight']:
                            has_pr = True
                        elif current_weight == hist['best_weight'] and current_reps > hist['best_weight_reps']:
                            has_strength_increase = True
        
        # Add emoji indicators
        workout['has_pr'] = has_pr
        workout['has_strength_increase'] = has_strength_increase
    
    return jsonify({
        'success': True,
        'workouts': workouts[:100]  # Last 100 entries
    })

def parse_workout_entries(content):
    """Parse workout entries from markdown content"""
    entries = []
    lines = content.split('\n')
    current_date = None
    current_text = []
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines at start and "Workout" header
        if not stripped and not current_date:
            continue
        if stripped == "Workout":
            continue
            
        # Check if line is a date (various formats)
        date_patterns = [
            r'^\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s+(AM|PM)',  # 11/11/25 2:30 PM (with time, 12-hour)
            r'^\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}',  # 11/11/25 14:30 (with time, 24-hour - legacy)
            r'^\d{1,2}/\d{1,2}/\d{2,4}$',  # 11/11/25 or 11/11/2025
            r'^\d{4}-\d{2}-\d{2}',  # 2024-11-11
            r'^\d{1,2}-\d{1,2}-\d{2,4}',  # 11-11-25
            r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+\d{1,2}/\d{1,2}/\d{2,4}',  # Monday 11/11/19
            r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+\d{1,2}/\d{1,2}',  # Monday 11/11
        ]
        
        is_date = any(re.match(pattern, stripped, re.IGNORECASE) for pattern in date_patterns)
        
        if is_date:
            # Save previous entry
            if current_date and current_text:
                text = '\n'.join(current_text).strip()
                if text:  # Only add if there's actual content
                    entries.append({
                        'date': current_date,
                        'text': text
                    })
            # Extract date and time (if present)
            # Try to match date with time first: "11/11/25 2:30 PM" or "11/11/25 14:30"
            date_time_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]?\d{0,4})\s+(\d{1,2}:\d{2}(?:\s+(AM|PM))?)', stripped)
            if date_time_match:
                current_date = f"{date_time_match.group(1)} {date_time_match.group(2)}"
            else:
                # Just date, no time
                date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]?\d{0,4})', stripped)
                if date_match:
                    current_date = date_match.group(1)
                else:
                    current_date = stripped
            current_text = []
        elif current_date and stripped:
            # Add to current entry
            current_text.append(line.rstrip())  # Keep original line (with spacing)
    
    # Add last entry
    if current_date and current_text:
        text = '\n'.join(current_text).strip()
        if text:
            entries.append({
                'date': current_date,
                'text': text
            })
    
    return entries

@app.route('/api/log-workout', methods=['POST'])
def log_workout():
    """Log a new workout entry to database or file"""
    data = request.json
    workout_text = data.get('workout', '')
    
    if not workout_text:
        return jsonify({'error': 'Workout text required'}), 400
    
    # Prepend to workout log (newest at top)
    from datetime import datetime
    now = datetime.now()
    date_str = now.strftime('%m/%d/%y')
    time_str = now.strftime('%I:%M %p').lstrip('0')  # 12-hour format with AM/PM, remove leading zero from hour
    full_date = f"{date_str} {time_str}"
    
    # Try database first
    workout_id = add_workout_to_db(full_date, workout_text)
    
    if workout_id:
        # Successfully added to database
        # Still need to update search index and other logic
        pass
    else:
        # Fall back to file-based
        new_entry = f"\n{full_date}\n\n{workout_text}\n"
        
        # Read existing content
        existing = ""
        if WORKOUT_LOG.exists():
            existing = WORKOUT_LOG.read_text()
        
        # Write with new entry at top
        with open(WORKOUT_LOG, 'w') as f:
            f.write(new_entry + existing)
    
    # Incrementally update search index (rule-based categories)
    # New workout is at index 0 (prepended)
    try:
        # Parse the new workout to get PR status and muscle groups
        from workout_parser import parse_workout_text
        from datetime import datetime
        today = datetime.now()
        
        # Build workout data for index update
        workout_data = {
            'date': f"{date_str} {time_str}",
            'text': workout_text
        }
        
        # Check if this is a PR (compare to existing workouts)
        workouts = parse_workout_entries(new_entry + existing)
        if len(workouts) > 1:  # More than just this workout
            # Compare to previous workouts to detect PR
            parsed_new = parse_workout_text(workout_text)
            new_exercises = parsed_new.get('exercises', [])
            
            # Get history from existing workouts (skip first one which is the new one)
            history_before = {}
            for prev_workout in workouts[1:]:  # Skip new workout
                prev_parsed = parse_workout_text(prev_workout.get('text', ''))
                for ex in prev_parsed.get('exercises', []):
                    ex_key = ex['exercise'].lower().strip()
                    max_weight = ex.get('max_weight', 0)
                    first_reps = ex.get('first_reps', 0)
                    
                    if ex_key not in history_before:
                        history_before[ex_key] = {
                            'best_weight': max_weight,
                            'best_reps': first_reps
                        }
                    else:
                        if max_weight > history_before[ex_key]['best_weight']:
                            history_before[ex_key]['best_weight'] = max_weight
                        if first_reps > history_before[ex_key]['best_reps']:
                            history_before[ex_key]['best_reps'] = first_reps
            
            # Check for PR
            has_pr = False
            for ex in new_exercises:
                ex_key = ex['exercise'].lower().strip()
                current_weight = ex.get('max_weight', 0)
                current_reps = ex.get('first_reps', 0)
                is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                
                if ex_key in history_before:
                    hist = history_before[ex_key]
                    if is_bodyweight:
                        if current_reps > hist['best_reps']:
                            has_pr = True
                            break
                    else:
                        if current_weight > hist['best_weight']:
                            has_pr = True
                            break
            
            workout_data['has_pr'] = has_pr
        
        # Update index incrementally (new workout is at index 0)
        update_index_for_workout(0, workout_data, operation='add')
        
        # Trigger background rebuild for AI categories
        rebuild_ai_index_async()
    except Exception as e:
        print(f"Error updating search index: {e}")
        # Fallback: trigger full rebuild in background
        rebuild_ai_index_async()
    
    return jsonify({
        'success': True,
        'message': 'Workout logged',
        'entry': {
            'date': f"{date_str} {time_str}",
            'text': workout_text
        }
    })

@app.route('/api/post-workout-insight', methods=['POST'])
@require_auth
def post_workout_insight():
    """Generate AI insight after logging a workout - data-driven, detects PRs and strength increases"""
    data = request.json
    workout_text = data.get('workout', '')
    
    if not workout_text:
        return jsonify({'error': 'Workout text required'}), 400
    
    # Parse the current workout
    from workout_parser import parse_workout_text
    from datetime import datetime
    parsed = parse_workout_text(workout_text)
    exercises = parsed.get('exercises', [])
    
    if not exercises:
        return jsonify({
            'success': True,
            'insight': 'Workout logged!'
        })
    
    # Get workout history for current user only to compare for PRs
    user_id = get_current_user_id()
    workouts = get_workouts_from_db(user_id, limit=30) or []
    
    # Build exercise history lookup
    exercise_history = {}
    today = datetime.now()
    
    for workout in workouts[1:31]:  # Last 30 workouts (skip the one just logged)
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            max_weight = ex.get('max_weight', 0)
            first_reps = ex.get('first_reps', 0)
            
            # Track best performance for each exercise
            if ex_key not in exercise_history:
                exercise_history[ex_key] = {
                    'best_weight': max_weight,
                    'best_reps': first_reps,
                    'best_weight_reps': first_reps if max_weight > 0 else 0
                }
            else:
                # Update if this is better
                if max_weight > exercise_history[ex_key]['best_weight']:
                    exercise_history[ex_key]['best_weight'] = max_weight
                    exercise_history[ex_key]['best_weight_reps'] = first_reps
                if first_reps > exercise_history[ex_key]['best_reps']:
                    exercise_history[ex_key]['best_reps'] = first_reps
    
    # Detect PRs and improvements
    prs = []
    improvements = []
    
    for ex in exercises:
        ex_key = ex['exercise'].lower().strip()
        current_weight = ex.get('max_weight', 0)
        current_reps = ex.get('first_reps', 0)
        is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
        
        if ex_key in exercise_history:
            hist = exercise_history[ex_key]
            
            if is_bodyweight:
                # Bodyweight PR: more reps
                if current_reps > hist['best_reps']:
                    prs.append(f"{ex['exercise']} ({current_reps} reps, previous best: {hist['best_reps']})")
            else:
                # Weighted PR: higher weight OR same weight with more reps
                if current_weight > hist['best_weight']:
                    prs.append(f"{ex['exercise']} ({current_weight}lbs, previous best: {hist['best_weight']}lbs)")
                elif current_weight == hist['best_weight'] and current_reps > hist['best_weight_reps']:
                    improvements.append(f"{ex['exercise']} (+{current_reps - hist['best_weight_reps']} reps at {current_weight}lbs)")
    
    # Build factual insight (rule-based)
    fact_part = ""
    if prs:
        if len(prs) == 1:
            fact_part = f"PR reached! {prs[0]}"
        else:
            fact_part = f"Big accomplishment! {len(prs)} new PRs: {', '.join(prs[:2])}"
    elif improvements:
        fact_part = f"Strength increase: {improvements[0]}"
    else:
        # No PRs, just acknowledge the workout
        exercise_count = len(exercises)
        fact_part = f"Logged {exercise_count} exercise{'s' if exercise_count != 1 else ''}"
    
    # Use AI to generate a natural, varied encouragement phrase (3-5 words)
    encouragement_prompt = f"""Generate a brief, encouraging phrase (3-5 words) for this workout achievement:

{fact_part}

Examples: "You're crushing it!", "Amazing work!", "Keep it up!", "You're a star!", "Nice job!", "Well done!"

Generate a fresh, natural encouragement phrase (3-5 words only, no punctuation needed):"""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=20,  # Very short - just a phrase
            messages=[{"role": "user", "content": encouragement_prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        encouragement = message.content[0].text.strip()
        # Remove any trailing punctuation the AI might add
        encouragement = encouragement.rstrip('.,!')
        
        insight = f"{fact_part}. {encouragement}!"
        
    except Exception as e:
        # Fallback to rule-based encouragement if AI fails
        if prs:
            encouragement = "You're a star"
        elif improvements:
            encouragement = "Keep it up"
        else:
            encouragement = "Great job"
        insight = f"{fact_part}. {encouragement}!"
    
    return jsonify({
        'success': True,
        'insight': insight
    })

@app.route('/api/recovery-check', methods=['GET'])
def recovery_check():
    """Check recovery status - which muscle groups are ready vs need rest"""
    from workout_parser import parse_workout_text, extract_muscle_groups_from_exercises
    from datetime import datetime, timedelta
    import json
    
    # Get user-specific workouts from database
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    workouts = []
    # Try database first - user-specific
    db_workouts = get_workouts_from_db(user_id, limit=20)
    if db_workouts:
        workouts = db_workouts
    elif not USE_DATABASE:
        # Fallback to file-based only if no database (legacy mode)
        if WORKOUT_LOG.exists():
            content = WORKOUT_LOG.read_text()
            if content.strip():
                workouts.extend(parse_workout_entries(content))
    
    # Track muscle group training dates
    muscle_group_last_trained = {}
    today = datetime.now()
    
    # Check last 14 days of workouts
    for workout in workouts[:20]:  # Check last 20 workouts
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        days_ago = (today - workout_date).days
        if days_ago > 14:
            continue
        
        # Parse workout and extract muscle groups
        parsed = parse_workout_text(workout.get('text', ''))
        muscle_groups = extract_muscle_groups_from_exercises(parsed.get('exercises', []))
        
        # Also infer additional groups from exercises (e.g., squats = legs + glutes)
        exercise_names = [ex['exercise'].lower() for ex in parsed.get('exercises', [])]
        for ex_name in exercise_names:
            # Infer glutes from leg exercises
            if any(word in ex_name for word in ['squat', 'lunge', 'split', 'hip', 'glute']):
                muscle_groups.append('glutes')
            # Infer calves from calf-specific exercises
            if 'calf' in ex_name:
                muscle_groups.append('calves')
            # Infer abs from core exercises
            if any(word in ex_name for word in ['crunch', 'sit-up', 'plank', 'ab', 'core']):
                muscle_groups.append('abs')
        
        # Track most recent training date for each muscle group
        for group in muscle_groups:
            if group not in muscle_group_last_trained or days_ago < muscle_group_last_trained[group]:
                muscle_group_last_trained[group] = days_ago
    
    # Categorize muscle groups by recovery status
    # 0-1 days: Too soon (need rest)
    # 2-3 days: Optimal recovery window
    # 4+ days: Ready (could train)
    # 7+ days: Neglected (should train)
    
    too_soon = []  # 0-1 days
    optimal = []   # 2-3 days
    ready = []     # 4-6 days
    neglected = [] # 7+ days or never trained
    
    # Comprehensive full body health categories
    # Upper body: chest, back, shoulders, arms (biceps/triceps)
    # Lower body: legs (quads/hamstrings), glutes, calves
    # Core: abs, lower back
    # Note: We track both specific (biceps, triceps) and general (arms) to handle different mappings
    all_groups = [
        'chest', 'back', 'shoulders', 
        'arms', 'biceps', 'triceps',
        'legs', 'glutes', 'calves',
        'core', 'abs'
    ]
    
    # Also check for groups that might be mapped differently
    # (e.g., "legs" might include glutes, but we want to track separately if possible)
    for group in all_groups:
        if group in muscle_group_last_trained:
            days = muscle_group_last_trained[group]
            if days <= 1:
                too_soon.append((group, days))
            elif days <= 3:
                optimal.append((group, days))
            elif days <= 6:
                ready.append((group, days))
            else:
                neglected.append((group, days))
        else:
            # Never trained (or not in last 14 days) - mark as neglected for full body health
            neglected.append((group, None))
    
    # Remove duplicates (e.g., if both "arms" and "biceps" are in neglected)
    # Prioritize more specific groups (biceps > arms)
    seen_general = set()
    filtered_neglected = []
    for group, days in neglected:
        # Skip general groups if specific ones are present
        if group == 'arms' and ('biceps' in [g[0] for g in neglected] or 'triceps' in [g[0] for g in neglected]):
            continue
        filtered_neglected.append((group, days))
    neglected = filtered_neglected
    
    # Build recovery message - only show Neglected and Ready to train
    messages = []
    
    # Priority 1: Neglected (should train these)
    if neglected:
        groups_str = ', '.join([g[0] for g in neglected[:3]])  # Top 3
        messages.append(f"<strong>Neglected:</strong> {groups_str}")
    
    # Priority 2: Ready to train (4-6 days, good to go)
    if ready:
        groups_str = ', '.join([f"{g[0]} ({g[1]} days ago)" for g in ready])
        messages.append(f"<strong>Ready to train:</strong> {groups_str}")
    
    if not messages:
        recovery_status = "No recent workout data"
        formatted_status = recovery_status
    else:
        # Use line breaks between categories
        recovery_status = " • ".join([msg.replace("<strong>", "").replace("</strong>", "") for msg in messages])
        formatted_status = "<br>".join(messages)
    
    return jsonify({
        'success': True,
        'recovery_status': recovery_status,
        'recovery_status_formatted': formatted_status,
        'too_soon': [g[0] for g in too_soon],
        'optimal': [g[0] for g in optimal],
        'ready': [g[0] for g in ready],
        'neglected': [g[0] for g in neglected],
        'neglected_groups': [g[0] for g in neglected]  # For workout generation
    })

@app.route('/api/update-workout', methods=['POST'])
def update_workout():
    """Update an existing workout entry in database or file"""
    data = request.json
    old_date = data.get('old_date')
    old_text = data.get('old_text')
    new_text = data.get('new_text')
    
    if not all([old_date, old_text, new_text]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Try database first
    if update_workout_in_db(old_date, old_text, new_text):
        return jsonify({'success': True, 'message': 'Workout updated'})
    
    # Fall back to file-based
    if not WORKOUT_LOG.exists():
        return jsonify({'error': 'Workout log not found'}), 404
    
    content = WORKOUT_LOG.read_text()
    
    # Replace the old entry with new text
    old_entry = f"{old_date}\n\n{old_text}"
    new_entry = f"{old_date}\n\n{new_text}"
    
    if old_entry in content:
        content = content.replace(old_entry, new_entry, 1)
        WORKOUT_LOG.write_text(content)
        
        # Incrementally update search index
        try:
            # Find workout index
            workouts = parse_workout_entries(content)
            workout_index = None
            for i, workout in enumerate(workouts):
                if workout.get('date') == old_date and workout.get('text') == new_text:
                    workout_index = i
                    break
            
            if workout_index is not None:
                # Parse new workout to get PR status and muscle groups
                from workout_parser import parse_workout_text
                from datetime import datetime
                today = datetime.now()
                
                workout_data = {
                    'date': old_date,
                    'text': new_text
                }
                
                # Check if this is a PR (compare to other workouts)
                if len(workouts) > 1:
                    parsed_new = parse_workout_text(new_text)
                    new_exercises = parsed_new.get('exercises', [])
                    
                    # Get history from other workouts
                    history_before = {}
                    for prev_workout in workouts:
                        if prev_workout.get('date') == old_date and prev_workout.get('text') == new_text:
                            continue  # Skip current workout
                        
                        prev_parsed = parse_workout_text(prev_workout.get('text', ''))
                        for ex in prev_parsed.get('exercises', []):
                            ex_key = ex['exercise'].lower().strip()
                            max_weight = ex.get('max_weight', 0)
                            first_reps = ex.get('first_reps', 0)
                            
                            if ex_key not in history_before:
                                history_before[ex_key] = {
                                    'best_weight': max_weight,
                                    'best_reps': first_reps
                                }
                            else:
                                if max_weight > history_before[ex_key]['best_weight']:
                                    history_before[ex_key]['best_weight'] = max_weight
                                if first_reps > history_before[ex_key]['best_reps']:
                                    history_before[ex_key]['best_reps'] = first_reps
                    
                    # Check for PR
                    has_pr = False
                    for ex in new_exercises:
                        ex_key = ex['exercise'].lower().strip()
                        current_weight = ex.get('max_weight', 0)
                        current_reps = ex.get('first_reps', 0)
                        is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                        
                        if ex_key in history_before:
                            hist = history_before[ex_key]
                            if is_bodyweight:
                                if current_reps > hist['best_reps']:
                                    has_pr = True
                                    break
                            else:
                                if current_weight > hist['best_weight']:
                                    has_pr = True
                                    break
                    
                    workout_data['has_pr'] = has_pr
                
                # Update index incrementally
                update_index_for_workout(workout_index, workout_data, operation='update')
                
                # Trigger background rebuild for AI categories
                rebuild_ai_index_async()
        except Exception as e:
            print(f"Error updating search index: {e}")
            rebuild_ai_index_async()
        
        return jsonify({'success': True, 'message': 'Workout updated'})
    else:
        return jsonify({'error': 'Workout entry not found'}), 404

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    """Submit user feedback"""
    data = request.json
    feedback_text = data.get('feedback', '').strip()
    
    if not feedback_text:
        return jsonify({'error': 'Feedback text required'}), 400
    
    # Load existing feedback
    feedback_list = []
    if FEEDBACK_LOG.exists():
        try:
            feedback_list = json.loads(FEEDBACK_LOG.read_text())
        except:
            feedback_list = []
    
    # Add new feedback with timestamp and metadata
    from datetime import datetime
    metadata = data.get('metadata', {})
    timestamp = datetime.now()
    
    feedback_metadata = {
        # App state
        'workout_count': metadata.get('workoutCount', 0),
        'recovery_check_visible': metadata.get('hasRecoveryCheck', False),
        'analytics_open': metadata.get('analyticsOpen', False),
        'search_active': metadata.get('searchActive', False),
        'search_query': metadata.get('searchQuery'),
        'last_workout_date': metadata.get('lastWorkoutDate'),
        
        # Device/Technical
        'screen_width': metadata.get('screenWidth'),
        'screen_height': metadata.get('screenHeight'),
        'device_type': metadata.get('deviceType'),
        'url': metadata.get('url'),
    }
    
    # Try database first
    if USE_DATABASE:
        try:
            import json as json_lib
            user_id = get_current_user_id()
            if user_id:
                try:
                    user_id = int(user_id)
                except (ValueError, TypeError):
                    user_id = None
            
            if user_id:
                db_url = get_db_url()
                use_sqlite = is_sqlite(db_url)
                with get_db_connection() as conn:
                    cur = get_cursor(conn)
                    if use_sqlite:
                        cur.execute("""
                            INSERT INTO feedback (text, timestamp, user_agent, metadata, user_id)
                            VALUES (?, ?, ?, ?, ?)
                        """, (feedback_text, timestamp, request.headers.get('User-Agent', ''), json_lib.dumps(feedback_metadata), user_id))
                    else:
                        cur.execute("""
                            INSERT INTO feedback (text, timestamp, user_agent, metadata, user_id)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (feedback_text, timestamp, request.headers.get('User-Agent', ''), json_lib.dumps(feedback_metadata), user_id))
                return jsonify({
                    'success': True,
                    'message': 'Feedback submitted. Thank you!'
                })
        except Exception as e:
            print(f"Error saving feedback to database: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to file-based
    
    # File-based fallback
    feedback_list = []
    if FEEDBACK_LOG.exists():
        try:
            feedback_list = json.loads(FEEDBACK_LOG.read_text())
        except:
            feedback_list = []
    
    feedback_entry = {
        'text': feedback_text,
        'timestamp': timestamp.isoformat(),
        'user_agent': request.headers.get('User-Agent', ''),
        'metadata': feedback_metadata
    }
    
    feedback_list.append(feedback_entry)
    
    # Save feedback
    FEEDBACK_LOG.write_text(json.dumps(feedback_list, indent=2))
    
    return jsonify({
        'success': True,
        'message': 'Feedback submitted. Thank you!'
    })

@app.route('/api/generate-theme', methods=['POST'])
def generate_theme():
    """Generate a theme for a workout entry (only if it doesn't exist)"""
    data = request.json
    workout_date = data.get('workout_date', '')
    workout_text = data.get('workout_text', '')
    
    if not workout_text:
        return jsonify({'error': 'Workout text required'}), 400
    
    # Check if theme already exists
    workout_key = get_workout_key(workout_date, workout_text)
    
    # Try database first
    existing_theme = get_theme_from_db(workout_key)
    if existing_theme:
        return jsonify({
            'success': True,
            'theme': existing_theme,
            'cached': True
        })
    
    # Fall back to file-based check
    themes = load_themes()
    if workout_key in themes:
        # Theme already exists, return it
        return jsonify({
            'success': True,
            'theme': themes[workout_key],
            'cached': True
        })
    
    # Generate new theme
    # Budget check removed - themes are cheap and important for UX
    
    prompt = f"""Read this workout entry and write a very short theme (5 words or less) that captures what this workout was about.

Workout:
{workout_text[:1000]}

Write a very concise theme - just the workout type/focus. Examples:
- "Upper body workout"
- "Leg day"
- "Chest and tricep"
- "Full body"
- "Shoulder and bicep"

Keep it to 5 words maximum. Just the workout type, nothing else.

Theme:"""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=20,  # Very short - just a few words
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        theme = message.content[0].text.strip()
        
        # Save theme to database or file
        if not save_theme_to_db(workout_key, theme):
            # Fall back to file-based
            themes = load_themes()
            themes[workout_key] = theme
            save_themes(themes)
        
        return jsonify({
            'success': True,
            'theme': theme,
            'cached': False
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update-theme', methods=['POST'])
def update_theme():
    """Manually update a theme"""
    data = request.json
    workout_date = data.get('workout_date', '')
    workout_text = data.get('workout_text', '')
    new_theme = data.get('theme', '').strip()
    
    if not all([workout_date, workout_text, new_theme]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Save updated theme
    themes = load_themes()
    workout_key = get_workout_key(workout_date, workout_text)
    themes[workout_key] = new_theme
    save_themes(themes)
    
    return jsonify({
        'success': True,
        'theme': new_theme
    })

@app.route('/api/get-last-workout', methods=['GET'])
def get_last_workout():
    """Get a workout that targets muscle groups that need work (haven't been done recently)"""
    # Get user-specific workouts - require authentication
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    workouts = []
    # Get user-specific workouts from database
    db_workouts = get_workouts_from_db(user_id, limit=50)
    if db_workouts:
        workouts = db_workouts
    elif not USE_DATABASE:
        # Fallback to file-based only if no database (legacy mode)
        if WORKOUT_LOG.exists():
            content = WORKOUT_LOG.read_text()
            if content.strip():
                workouts.extend(parse_workout_entries(content))
    
    if not workouts:
        return jsonify({
            'success': True,
            'workout': 'Start with your favorite exercises!'
        })
    
    # Simple approach: Find the workout that was done longest ago (most neglected)
    from datetime import datetime
    today = datetime.now()
    
    oldest_workout = None
    oldest_days_ago = 0
    
    for workout in workouts[:50]:  # Check last 50 workouts
        workout_date_str = workout.get('date', '')
        workout_text = workout.get('text', '').strip()
        
        if not workout_text:
            continue
        
        # Parse date
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    workout_date = datetime.strptime(workout_date_str, fmt)
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        days_ago = (today - workout_date).days
        
        # Find the workout that's been done longest ago
        if days_ago > oldest_days_ago:
            oldest_days_ago = days_ago
            oldest_workout = workout
    
    # Use the oldest workout (most neglected), or fall back to most recent if none found
    if oldest_workout and oldest_days_ago >= 7:  # Only use if it's been 7+ days
        default_workout = oldest_workout.get('text', '').strip()
        print(f"DEBUG: Using oldest workout from {oldest_workout.get('date', 'unknown')} ({oldest_days_ago} days ago)")
    else:
        # Fall back to most recent if no old workouts found
        default_workout = workouts[0].get('text', '').strip()
        print(f"DEBUG: No old workouts found (oldest was {oldest_days_ago} days), using most recent")
    
    return jsonify({
        'success': True,
        'workout': default_workout
    })

@app.route('/api/progressive-overload-suggestions', methods=['POST'])
def progressive_overload_suggestions():
    """Get progressive overload suggestions for a workout (just suggestions, not full workout)"""
    data = request.json
    current_workout = data.get('workout', '').strip()
    
    if not current_workout:
        return jsonify({'error': 'No workout provided'}), 400
    
    # Parse the current workout
    from workout_parser import parse_workout_text
    parsed = parse_workout_text(current_workout)
    exercises = parsed.get('exercises', [])
    
    if not exercises:
        return jsonify({
            'success': True,
            'suggestions': []
        })
    
    # Get user-specific workout history to find last performance for each exercise
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Get workouts for current user only from database
    workouts = get_workouts_from_db(user_id, limit=30) or []
    
    # Build exercise history lookup
    exercise_last_done = {}
    from datetime import datetime
    today = datetime.now()
    
    for workout in workouts[:30]:  # Check last 30 workouts
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            # Try formats with time first, then without
            # Support both 12-hour (AM/PM) and 24-hour formats for backward compatibility
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    # Fix 2-digit years: if year is > current year + 1, assume it's in the past century
                    # e.g., if we're in 2024 and date is 2025, it's probably 1925 or should be ignored
                    if parsed_date.year > today.year + 1:
                        # Likely a parsing error, skip this format
                        continue
                    # If date is in the future (more than 1 day ahead), it's probably wrong - skip
                    if (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            days_ago = (today - workout_date).days
            
            # Skip if days_ago is negative (future date) or unreasonably large (> 10 years)
            if days_ago < 0 or days_ago > 3650:
                continue
            
            # Track the most recent performance (smallest days_ago)
            if ex_key not in exercise_last_done or days_ago < exercise_last_done[ex_key]['days_ago']:
                # Get reps from heaviest set (not max_reps which could be from drop sets)
                max_weight = ex.get('max_weight', 0)
                sets = ex.get('sets', [])
                is_bodyweight = ex.get('is_bodyweight', False) or max_weight == 0
                
                if is_bodyweight:
                    # For bodyweight exercises, use first_reps (first set reps)
                    reps_at_max_weight = ex.get('first_reps', 0)
                else:
                    # For weighted exercises, get reps from heaviest set
                    reps_at_max_weight = max_weight
                    for s in sets:
                        if s['weight'] == max_weight:
                            reps_at_max_weight = s['reps']
                            break
                
                exercise_last_done[ex_key] = {
                    'exercise': ex['exercise'],
                    'days_ago': days_ago,
                    'weight': max_weight,
                    'reps': reps_at_max_weight
                }
    
    # Generate suggestions for each exercise
    suggestions = []
    for ex in exercises:
        ex_name = ex['exercise']
        ex_key = ex_name.lower().strip()
        current_weight = ex.get('max_weight', 0)
        current_reps = ex.get('first_reps', 0)
        is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
        
        # Find last performance from history
        last_perf = exercise_last_done.get(ex_key)
        
        # Handle bodyweight exercises (weight = 0)
        if is_bodyweight and last_perf:
            last_weight = last_perf['weight']
            last_reps = last_perf['reps']
            days_ago = last_perf['days_ago']
            
            if days_ago <= 14:
                # Recent enough to progress
                if last_weight == 0:
                    # Pure bodyweight - suggest more reps or adding weight
                    if last_reps < 12:
                        # Increase reps at bodyweight
                        suggested_weight = 0
                        suggested_reps = min(last_reps + 1, 12)
                        reason = f"+1 rep (bodyweight progression)"
                    else:
                        # At 12+ reps, suggest adding weight
                        suggested_weight = 25  # Start with 25 lbs
                        suggested_reps = 5
                        reason = f"Add 25lbs (at {last_reps} reps, add weight)"
                else:
                    # Previously had weight, but now doing bodyweight - suggest matching or adding weight
                    suggested_weight = 0
                    suggested_reps = last_reps
                    reason = "Match last bodyweight performance"
            elif days_ago <= 30:
                suggested_weight = 0
                suggested_reps = last_reps
                reason = "Match last performance"
            else:
                suggested_weight = 0
                suggested_reps = max(1, last_reps - 1) if last_reps > 1 else last_reps
                reason = f"Slightly lighter (been {days_ago} days)"
            
            # Only add if different from current
            if suggested_weight != current_weight or suggested_reps != current_reps:
                if suggested_weight == 0:
                    display_current = f"{current_reps} reps"
                    display_suggested = f"{suggested_reps} reps"
                else:
                    display_current = f"{current_reps} reps"
                    display_suggested = f"{suggested_weight}lbs * {suggested_reps}"
                
                suggestions.append({
                    'exercise': ex_name,
                    'current': display_current,
                    'suggested': display_suggested,
                    'reason': reason,
                    'last_done': f"{days_ago} days ago",
                    'last_performance': f"{last_reps} reps" if last_weight == 0 else f"{last_weight}lbs * {last_reps}"
                })
            continue
        
        # Handle weighted exercises
        if last_perf and last_perf['weight'] > 0:
            last_weight = last_perf['weight']
            last_reps = last_perf['reps']
            days_ago = last_perf['days_ago']
            
            # Generate suggestion
            if days_ago <= 14:
                # Recent enough to progress
                # User's method: build to 5-6 reps, then increase weight
                if last_reps < 6:
                    # Increase reps at same weight (build to 5-6 reps)
                    suggested_weight = last_weight
                    suggested_reps = min(last_reps + 1, 6)
                    reason = f"+1 rep (build to 5-6 reps)"
                else:
                    # At 5-6 reps or more - increase weight
                    # Calculate 2.5% increase, but ensure minimum 2.5 lb increase
                    weight_increase = max(last_weight * 0.025, 2.5)
                    new_weight = last_weight + weight_increase
                    
                    # Round to nearest 2.5 or 5 lbs based on weight
                    if last_weight < 50:
                        suggested_weight = round(new_weight / 2.5) * 2.5
                    else:
                        suggested_weight = round(new_weight / 5) * 5
                    suggested_weight = int(suggested_weight)
                    
                    # Ensure we actually increased (minimum 2.5 lbs)
                    if suggested_weight <= last_weight:
                        suggested_weight = last_weight + 2.5 if last_weight < 50 else last_weight + 5
                        suggested_weight = int(suggested_weight)
                    
                    # Reset reps to 5 when increasing weight
                    suggested_reps = 5
                    weight_change = suggested_weight - last_weight
                    reason = f"+{weight_change}lbs (at {last_reps} reps, increase weight)"
            elif days_ago <= 30:
                # Match last performance (but still show it as a suggestion)
                suggested_weight = last_weight
                suggested_reps = last_reps
                reason = "Match last performance"
            else:
                # Start slightly lighter
                weight_decrease = last_weight * 0.05
                if last_weight < 50:
                    suggested_weight = max(1, int(round((last_weight - weight_decrease) / 2.5) * 2.5))
                else:
                    suggested_weight = max(1, int(round((last_weight - weight_decrease) / 5) * 5))
                suggested_reps = max(1, last_reps - 1) if last_reps > 1 else last_reps
                reason = f"Slightly lighter (been {days_ago} days)"
            
            # Always show suggestion if we have history (compare to last performance, not current workout)
            # The "current" shown is what's in the copied workout, "suggested" is based on last performance
            suggestions.append({
                'exercise': ex_name,
                'current': f"{current_weight} * {current_reps}",
                'suggested': f"{suggested_weight} * {suggested_reps}",
                'reason': reason,
                'last_done': f"{days_ago} days ago",
                'last_performance': f"{last_weight} * {last_reps}"
            })
    
    return jsonify({
        'success': True,
        'suggestions': suggestions
    })

@app.route('/api/progressive-overload', methods=['POST'])
def progressive_overload():
    """Apply progressive overload to the current workout - increase reps or weight"""
    data = request.json
    current_workout = data.get('workout', '').strip()
    
    if not current_workout:
        return jsonify({'error': 'No workout provided'}), 400
    
    # Parse the current workout
    from workout_parser import parse_workout_text
    parsed = parse_workout_text(current_workout)
    exercises = parsed.get('exercises', [])
    
    if not exercises:
        return jsonify({'error': 'Could not parse workout'}), 400
    
    # Get workout history to find last performance for each exercise
    workouts = get_workouts()
    
    # Build exercise history lookup
    exercise_last_done = {}
    from datetime import datetime
    today = datetime.now()
    
    for workout in workouts[:30]:  # Check last 30 workouts
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            # Try formats with time first, then without
            # Support both 12-hour (AM/PM) and 24-hour formats for backward compatibility
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    workout_date = datetime.strptime(workout_date_str, fmt)
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            days_ago = (today - workout_date).days
            
            # Track the most recent performance (smallest days_ago)
            if ex_key not in exercise_last_done or days_ago < exercise_last_done[ex_key]['days_ago']:
                # Get reps from heaviest set (not max_reps which could be from drop sets)
                max_weight = ex.get('max_weight', 0)
                sets = ex.get('sets', [])
                reps_at_max_weight = max_weight
                for s in sets:
                    if s['weight'] == max_weight:
                        reps_at_max_weight = s['reps']
                        break
                
                exercise_last_done[ex_key] = {
                    'exercise': ex['exercise'],
                    'days_ago': days_ago,
                    'weight': max_weight,
                    'reps': reps_at_max_weight
                }
    
    # Apply progressive overload to each exercise
    improved_lines = []
    for ex in exercises:
        ex_name = ex['exercise']
        ex_key = ex_name.lower().strip()
        
        # Find last performance from history
        last_perf = exercise_last_done.get(ex_key)
        
        if last_perf and last_perf['weight'] > 0:
            last_weight = last_perf['weight']
            last_reps = last_perf['reps']
            days_ago = last_perf['days_ago']
            
            # Apply progressive overload logic
            if days_ago <= 14:
                # Recent enough to progress
                if last_reps < 10 and last_reps + 1 <= 12:
                    # Increase reps at same weight
                    new_weight = last_weight
                    new_reps = min(last_reps + 1, 12)
                elif last_reps >= 10 and last_reps < 12:
                    # Increase weight (2.5% max) - already at 10+ reps
                    weight_increase = last_weight * 0.025
                    if last_weight < 50:
                        new_weight = round((last_weight + weight_increase) / 2.5) * 2.5
                    else:
                        new_weight = round((last_weight + weight_increase) / 5) * 5
                    new_weight = int(new_weight)
                    new_reps = min(last_reps, 12)  # Keep same reps, cap at 12
                else:
                    # At 12 reps - must increase weight
                    weight_increase = last_weight * 0.025
                    if last_weight < 50:
                        new_weight = round((last_weight + weight_increase) / 2.5) * 2.5
                    else:
                        new_weight = round((last_weight + weight_increase) / 5) * 5
                    new_weight = int(new_weight)
                    new_reps = 12
            elif days_ago <= 30:
                # Match last performance
                new_weight = last_weight
                new_reps = last_reps
            else:
                # Start slightly lighter
                weight_decrease = last_weight * 0.05
                if last_weight < 50:
                    new_weight = max(1, int(round((last_weight - weight_decrease) / 2.5) * 2.5))
                else:
                    new_weight = max(1, int(round((last_weight - weight_decrease) / 5) * 5))
                new_reps = max(1, last_reps - 1) if last_reps > 1 else last_reps
        else:
            # No history - use current workout's values as baseline
            current_weight = ex.get('max_weight', 0)
            current_reps = ex.get('first_reps', 0)
            if current_weight > 0 and current_reps > 0:
                # If no history but we have current values, keep them
                new_weight = current_weight
                new_reps = current_reps
            else:
                # No data at all - can't suggest
                new_weight = 0
                new_reps = 0
        
        # Format: "exercise - weight * reps" (only if we have valid values)
        if new_weight > 0 and new_reps > 0:
            improved_lines.append(f"{ex_name} - {new_weight} * {new_reps}")
    
    improved_workout = '\n'.join(improved_lines)
    
    return jsonify({
        'success': True,
        'workout': improved_workout
    })

@app.route('/api/remix-workout', methods=['POST'])
def remix_workout():
    """Remix the current workout - keep similar structure but vary exercises"""
    data = request.json
    current_workout = data.get('workout', '').strip()
    
    if not current_workout:
        return jsonify({'error': 'No workout provided'}), 400
    
    # Get workout history for context
    workouts = get_workouts()
    workout_history = "\n".join([f"{w.get('date', '')}\n{w.get('text', '')}" for w in workouts[:20]])
    
    # Use AI to remix the workout
    prompt = f"""Take this workout and create a remix/variation of it. Keep the same general structure and muscle groups, but vary the exercises slightly.

Current workout:
{current_workout}

Recent workout history for context:
{workout_history[:2000]}

Create a remix that:
- Targets the same muscle groups
- Uses similar exercise types (compound vs isolation)
- Varies specific exercises (e.g., if they have "dumbbell bench press", you could suggest "incline dumbbell press" or "barbell bench press")
- Keeps the same number of exercises
- Uses the same format: "exercise - weight * reps" (one set per exercise)

Return ONLY the workout exercises, one per line, in the exact format:
exercise - weight * reps

Do not include any summary, explanation, or extra text. Just the exercises."""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        remixed_workout = message.content[0].text.strip()
        
        return jsonify({
            'success': True,
            'workout': remixed_workout
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-default-exercise-count', methods=['GET'])
@require_auth
def get_default_exercise_count():
    """Calculate default exercise count based on historical average"""
    # Load workouts for current user only
    user_id = get_current_user_id()
    workouts = get_workouts_from_db(user_id, limit=30) or []
    
    # Calculate average exercises per workout
    from workout_parser import parse_workout_text
    
    exercise_counts = []
    for w in workouts[:30]:  # Last 30 workouts
        parsed = parse_workout_text(w.get('text', ''))
        count = parsed.get('exercise_count', 0)
        if count > 0:
            exercise_counts.append(count)
    
    if exercise_counts:
        avg_count = int(round(sum(exercise_counts) / len(exercise_counts)))
        # Clamp between 3 and 8
        default_count = max(3, min(8, avg_count))
    else:
        default_count = 5  # Default if no history
    
    return jsonify({
        'success': True,
        'count': default_count,
        'based_on_history': len(exercise_counts) > 0
    })

@app.route('/api/suggest-workout', methods=['GET'])
def suggest_workout():
    """Generate AI-powered workout suggestion based on recent history"""
    # Get user-specific workouts - require authentication
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Get exercise count from query parameter
    requested_count = request.args.get('count', type=int)
    
    # Get user-specific recent workout history (last 20 workouts)
    workouts = []
    db_workouts = get_workouts_from_db(user_id, limit=20)
    if db_workouts:
        workouts = db_workouts
    elif not USE_DATABASE:
        # Fallback to file-based only if no database (legacy mode)
        if WORKOUT_LOG.exists():
            content = WORKOUT_LOG.read_text()
            if content.strip():
                workouts.extend(parse_workout_entries(content))
    
    # Get last 20 workouts for analysis
    recent_workouts = workouts[:20]
    
    if not recent_workouts:
        return jsonify({
            'success': True,
            'suggestion': 'Start with a full body workout or choose your favorite exercises!'
        })
    
    # Load knowledge base first (needed for parsing)
    knowledge_base = load_knowledge_base()
    
    # Parse workouts to extract structured data for better suggestions
    from workout_parser import parse_workout_text, parse_exercise_line, extract_muscle_groups_from_exercises
    
    parsed_workouts = []
    for w in recent_workouts:
        parsed = parse_workout_text(w.get('text', ''))
        parsed_workouts.append({
            'date': w.get('date', ''),
            'text': w.get('text', ''),
            'exercises': parsed.get('exercises', []),
            'muscle_groups': extract_muscle_groups_from_exercises(parsed.get('exercises', []), knowledge_base)
        })
    
    # Build context from recent workouts (keep original format for AI)
    workout_history = "\n\n".join([
        f"Date: {w.get('date', 'Unknown')}\n{w.get('text', '')}"
        for w in recent_workouts
    ])
    
    # Build structured summary for individual exercise tracking
    structured_summary = ""
    if parsed_workouts:
        # Track when each individual exercise was last done
        from datetime import datetime
        from workout_parser import normalize_exercise_name
        
        exercise_last_done = {}
        today = datetime.now()
        
        # Go through all workouts (newest first) and track last time each exercise was done
        for workout in parsed_workouts:
            workout_date_str = workout.get('date', '')
            # Parse date
            workout_date = None
            if workout_date_str:
                for fmt in ['%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                    try:
                        workout_date = datetime.strptime(workout_date_str, fmt)
                        break
                    except:
                        continue
            
            if not workout_date:
                continue
            
            for ex in workout.get('exercises', []):
                ex_name = ex['exercise']
                # Normalize exercise name for consistent tracking
                normalized_name, _ = normalize_exercise_name(ex_name)
                
                # Only track if we haven't seen this exercise yet (since we're going newest to oldest)
                if normalized_name.lower() not in exercise_last_done:
                    days_ago = (today - workout_date).days
                    # Use reps from the heaviest set (max_weight), not max_reps which might be from drop sets
                    # Find the set with max_weight and use its reps
                    max_weight = ex.get('max_weight', 0)
                    sets = ex.get('sets', [])
                    reps_at_max_weight = ex.get('first_reps', ex.get('max_reps', 0))  # Fallback to first_reps or max_reps
                    
                    # Find reps from the set with max_weight
                    for s in sets:
                        if s.get('weight') == max_weight:
                            reps_at_max_weight = s.get('reps', reps_at_max_weight)
                            break
                    
                    exercise_last_done[normalized_name.lower()] = {
                        'exercise': normalized_name,
                        'days_ago': days_ago,
                        'date': workout_date_str,
                        'weight': max_weight,
                        'reps': reps_at_max_weight  # Use reps from heaviest set, not max_reps
                    }
        
        # Build summary - prioritize exercises not done recently
        structured_summary = "\n\nIndividual Exercise Tracking (when each exercise was last done):\n"
        
        # Sort by days ago (most recent first, but also show ones not done in a while)
        sorted_exercises = sorted(exercise_last_done.items(), key=lambda x: x[1]['days_ago'])
        
        # Show recent exercises (done in last 7 days) - AVOID these
        recent = [ex for ex in sorted_exercises if ex[1]['days_ago'] < 7]
        if recent:
            structured_summary += "\nExercises done recently (AVOID - need recovery):\n"
            for ex_key, ex_data in recent[:10]:
                structured_summary += f"- {ex_data['exercise']}: {ex_data['days_ago']} days ago ({ex_data['weight']} * {ex_data['reps']})\n"
        
        # Show exercises not done recently (7+ days) - PRIORITIZE these
        not_recent = [ex for ex in sorted_exercises if ex[1]['days_ago'] >= 7]
        if not_recent:
            structured_summary += "\nExercises not done recently (PRIORITIZE - ready to train):\n"
            for ex_key, ex_data in sorted(not_recent, key=lambda x: x[1]['days_ago'], reverse=True)[:15]:
                last_weight = ex_data.get('weight', 0)
                last_reps = ex_data.get('reps', 0)
                days_ago = ex_data['days_ago']
                
                # Calculate suggested progression based on progressive overload principles
                # Progressive overload = either increase reps at same weight OR increase weight by 2.5% max
                # Prefer rep increases (easier, safer) over weight increases
                if last_weight > 0:
                    if days_ago <= 14:
                        # Recent enough to progress - suggest EITHER:
                        # Option 1: Same weight, +1 rep (preferred - easier progression, but cap at 12 reps for hypertrophy)
                        # Option 2: +2.5% weight, same reps (if already at 10+ reps)
                        if last_reps < 10 and last_reps + 1 <= 12:
                            # Lower reps - suggest increasing reps at same weight (easier), but cap at 12
                            suggested_weight = last_weight
                            suggested_reps = min(last_reps + 1, 12)  # Cap at 12 reps (hypertrophy range)
                            progression_note = f"suggest: {suggested_weight} * {suggested_reps} (progressive overload: +1 rep at same weight)"
                        elif last_reps >= 10:
                            # Already at 10+ reps - suggest weight increase instead (don't go above 12 reps)
                            weight_increase = last_weight * 0.025  # 2.5% increase max
                            if last_weight < 50:
                                suggested_weight = round((last_weight + weight_increase) / 2.5) * 2.5
                            else:
                                suggested_weight = round((last_weight + weight_increase) / 5) * 5
                            suggested_weight = int(suggested_weight)
                            suggested_reps = min(last_reps, 12)  # Keep same reps but cap at 12
                            progression_note = f"suggest: {suggested_weight} * {suggested_reps} (progressive overload: +{suggested_weight - last_weight}lbs - already at {last_reps} reps, increase weight instead)"
                        else:
                            # At 12 reps already - must increase weight
                            weight_increase = last_weight * 0.025  # 2.5% increase max
                            if last_weight < 50:
                                suggested_weight = round((last_weight + weight_increase) / 2.5) * 2.5
                            else:
                                suggested_weight = round((last_weight + weight_increase) / 5) * 5
                            suggested_weight = int(suggested_weight)
                            suggested_reps = 12  # Cap at 12
                            progression_note = f"suggest: {suggested_weight} * {suggested_reps} (progressive overload: +{suggested_weight - last_weight}lbs - at max reps, increase weight)"
                    elif days_ago <= 30:
                        # Been a while - match last performance
                        suggested_weight = last_weight
                        suggested_reps = last_reps
                        progression_note = f"suggest: {suggested_weight} * {suggested_reps} (match last performance)"
                    else:
                        # Been a long time - start slightly lighter (round to sensible increments)
                        weight_decrease = last_weight * 0.05  # 5% decrease
                        if last_weight < 50:
                            suggested_weight = max(1, int(round((last_weight - weight_decrease) / 2.5) * 2.5))
                        else:
                            suggested_weight = max(1, int(round((last_weight - weight_decrease) / 5) * 5))
                        suggested_reps = max(1, last_reps - 1) if last_reps > 1 else last_reps
                        progression_note = f"suggest: {suggested_weight} * {suggested_reps} (slightly lighter - been {days_ago} days)"
                else:
                    # No weight data available
                    suggested_weight = 0
                    suggested_reps = last_reps if last_reps > 0 else 8  # Default to 8 reps if unknown
                    progression_note = f"suggest: use reasonable starting weight * {suggested_reps} (no weight history)"
                
                structured_summary += f"- {ex_data['exercise']}: {days_ago} days ago (last: {last_weight} * {last_reps}) → {progression_note}\n"
        
        structured_summary += "\nCRITICAL: Prioritize exercises that haven't been done in 7+ days. Avoid exercises done in the last 7 days."
        structured_summary += "\n\nPROGRESSIVE OVERLOAD GUIDELINES:"
        structured_summary += "\n- If exercise was done 7-14 days ago: Progressive overload = EITHER increase reps at same weight (+1 rep) OR increase weight by 2.5% max"
        structured_summary += "\n  * Prefer rep increases (easier, safer) - if last reps < 10, suggest same weight +1 rep (max 12 reps for hypertrophy)"
        structured_summary += "\n  * If already at 10+ reps, suggest weight increase (2.5% max) instead - don't suggest reps above 12"
        structured_summary += "\n  * Rep range for hypertrophy: 6-12 reps. If at 12 reps, must increase weight, not reps"
        structured_summary += "\n- If exercise was done 14-30 days ago: Match last performance (weight * reps)"
        structured_summary += "\n- If exercise was done 30+ days ago: Start slightly lighter (5% less weight or 1 less rep) to rebuild"
        structured_summary += "\n- Always base suggestions on the user's actual last performance shown above (reps from heaviest set, not drop sets)"
    
    # Get knowledge summary for science-backed recommendations
    knowledge_summary = get_knowledge_summary(knowledge_base)
    
    # Analyze workout patterns for smarter suggestions
    def analyze_workout_patterns(workouts, knowledge_base):
        """Analyze workout history to identify patterns, recovery needs, and progression opportunities"""
        if not workouts:
            return {}
        
        # Load muscle group categorization
        muscle_groups = {}
        if knowledge_base and "muscle_groups" in knowledge_base and "categorization" in knowledge_base["muscle_groups"]:
            muscle_groups = knowledge_base["muscle_groups"]["categorization"]
        
        # Parse dates and extract muscle groups trained
        workout_analysis = []
        for w in workouts:
            date_str = w.get('date', '')
            workout_text = w.get('text', '').lower()
            
            # Try to parse date
            date_obj = None
            if date_str:
                for fmt in ['%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                    try:
                        date_obj = datetime.strptime(date_str, fmt)
                        break
                    except:
                        continue
            
            if not date_obj:
                continue
            
            # Identify muscle groups trained using parsed exercises (more accurate)
            trained_groups = []
            # Use parsed exercises if available
            parsed = parse_workout_text(w.get('text', ''))
            if parsed and parsed.get('exercises'):
                for ex in parsed['exercises']:
                    ex_name = ex['exercise'].lower()
                    for group, info in muscle_groups.items():
                        if isinstance(info, dict) and "primary_exercises" in info:
                            exercises = info["primary_exercises"]
                            for exercise in exercises:
                                if exercise.lower() in ex_name:
                                    if group not in trained_groups:
                                        trained_groups.append(group)
                                    break
            # Fallback to keyword matching if parsing failed
            if not trained_groups:
                for group, info in muscle_groups.items():
                    if isinstance(info, dict) and "primary_exercises" in info:
                        exercises = info["primary_exercises"]
                        for exercise in exercises:
                            if exercise.lower() in workout_text:
                                trained_groups.append(group)
                                break
            
            workout_analysis.append({
                'date': date_obj,
                'text': workout_text,
                'muscle_groups': list(set(trained_groups))  # Remove duplicates
            })
        
        # Calculate recovery status for each muscle group
        recovery_status = {}
        today = datetime.now()
        
        for group in muscle_groups.keys():
            # Find most recent workout for this muscle group
            last_trained = None
            for w in workout_analysis:
                if group in w['muscle_groups']:
                    if last_trained is None or w['date'] > last_trained:
                        last_trained = w['date']
            
            if last_trained:
                hours_since = (today - last_trained).total_seconds() / 3600
                days_since = hours_since / 24
                
                # Get recovery guidelines from knowledge base
                recovery_hours = 48  # Default
                if knowledge_base and "recovery" in knowledge_base:
                    rec = knowledge_base["recovery"].get("muscle_group_recovery", {})
                    recovery_hours = rec.get("minimum_hours", 48)
                
                recovery_status[group] = {
                    'last_trained': last_trained.strftime('%Y-%m-%d'),
                    'hours_since': hours_since,
                    'days_since': days_since,
                    'recovered': hours_since >= recovery_hours,
                    'ready': hours_since >= recovery_hours * 1.5  # More than minimum
                }
            else:
                recovery_status[group] = {
                    'last_trained': 'never',
                    'hours_since': None,
                    'days_since': None,
                    'recovered': True,  # Never trained = ready to train
                    'ready': True
                }
        
        # Identify muscle groups that haven't been trained recently
        ready_groups = [g for g, status in recovery_status.items() if status['ready']]
        needs_recovery = [g for g, status in recovery_status.items() if not status['recovered']]
        
        # Analyze frequency patterns
        frequency_analysis = {}
        for group in muscle_groups.keys():
            workouts_count = sum(1 for w in workout_analysis if group in w['muscle_groups'])
            # Count workouts in last 14 days
            recent_count = sum(1 for w in workout_analysis 
                             if group in w['muscle_groups'] 
                             and (today - w['date']).days <= 14)
            frequency_analysis[group] = {
                'total_workouts': workouts_count,
                'recent_workouts_14d': recent_count,
                'frequency': recent_count / 2 if recent_count > 0 else 0  # Approx times per week
            }
        
        return {
            'recovery_status': recovery_status,
            'ready_groups': ready_groups,
            'needs_recovery': needs_recovery,
            'frequency_analysis': frequency_analysis,
            'total_workouts_analyzed': len(workout_analysis)
        }
    
    # Perform pattern analysis
    pattern_analysis = analyze_workout_patterns(recent_workouts, knowledge_base)
    
    # Calculate days since last workout
    days_since_last = None
    if recent_workouts:
        # Try to get most recent date
        for w in recent_workouts:
            date_str = w.get('date', '')
            if date_str:
                for fmt in ['%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                    try:
                        date_obj = datetime.strptime(date_str, fmt)
                        if days_since_last is None or (datetime.now() - date_obj).days < days_since_last:
                            days_since_last = (datetime.now() - date_obj).days
                        break
                    except:
                        continue
    
    # Build pattern analysis summary with specific recovery times
    pattern_summary = ""
    if pattern_analysis:
        ready = pattern_analysis.get('ready_groups', [])
        needs_recovery = pattern_analysis.get('needs_recovery', [])
        freq = pattern_analysis.get('frequency_analysis', {})
        recovery_status = pattern_analysis.get('recovery_status', {})
        
        if ready:
            ready_details = []
            for group in ready[:5]:
                status = recovery_status.get(group, {})
                days = status.get('days_since', 0)
                if days:
                    # Round to whole number
                    days_rounded = int(round(days))
                    ready_details.append(f"{group} ({days_rounded} days ago)")
                else:
                    ready_details.append(group)
            pattern_summary += f"\nMuscle groups ready to train (adequate recovery, 2+ days): {', '.join(ready_details)}"
        if needs_recovery:
            recovery_details = []
            for group in needs_recovery[:5]:
                status = recovery_status.get(group, {})
                days = status.get('days_since', 0)
                if days:
                    # Round to whole number
                    days_rounded = int(round(days))
                    recovery_details.append(f"{group} ({days_rounded} days ago - needs more time, DO NOT suggest)")
                else:
                    recovery_details.append(group)
            pattern_summary += f"\nMuscle groups need more recovery (less than 48 hours - DO NOT suggest these): {', '.join(recovery_details)}"
        
        # Find groups that haven't been trained recently or frequently
        under_trained = []
        for group, data in freq.items():
            if data['recent_workouts_14d'] < 2:  # Less than 2x in last 2 weeks
                status = recovery_status.get(group, {})
                days = status.get('days_since')
                if days is not None:
                    # Round to whole number
                    days_rounded = int(round(days))
                    under_trained.append(f"{group} ({days_rounded} days ago)")
                else:
                    under_trained.append(f"{group} (never)")
        if under_trained:
            pattern_summary += f"\nMuscle groups that haven't been trained frequently: {', '.join(under_trained[:5])}"
    
    # Build science-backed context
    science_context = ""
    if knowledge_summary:
        science_context = f"""

Science-Backed Training Principles:
{knowledge_summary}
"""
    
    recovery_note = ""
    if days_since_last is not None:
        if days_since_last < 1:
            recovery_note = " Note: You worked out today or yesterday - ensure you're not training the same muscle groups without adequate recovery."
        elif days_since_last >= 3:
            recovery_note = " Note: It's been 3+ days since your last workout - you're well recovered and ready for any muscle group."
    
    # Determine exercise count - always generate 12-15 exercises (we'll show subset based on user preference)
    # This allows + and - buttons to work instantly without new API calls
    max_exercises_to_generate = 15  # Generate more than needed
    exercise_count = requested_count if requested_count else 5  # Default to 5 if not specified
    
    prompt = f"""Analyze this workout history and suggest what workout to do today.

Recent Workout History (most recent first):
{workout_history[:3000]}

Days since last workout: {days_since_last if days_since_last is not None else 'Unknown'}{recovery_note}
{pattern_summary}
{structured_summary}
{science_context}

Create a workout suggestion in this EXACT format:

5-word summary max (NO brackets, just plain text)

Workout exercises in the user's exact format - IMPORTANT: Show only ONE set as a suggestion
(Leave a blank line between the summary line and the exercises)
Example format: "dumbbell shoulder press - 75 * 6"
NOT: "dumbbell shoulder press - 75 * 6, 5, 4" (that's for logging, not suggestions)
The user will fill in the remaining sets themselves. Just suggest: exercise - weight * reps
Match the user's exercise names and style exactly

CRITICAL REQUIREMENTS:
1. First line: exactly 5 words or less - NO "Suggestion:" prefix, NO brackets [ ], just plain summary text
2. Remaining lines: Workout exercises in user's exact format (one set per exercise)
3. NO justification line - just summary + exercises
4. NO brackets anywhere in the output - the summary should be plain text like "Upper body strength day" not "[Upper body strength day]"

Match the user's writing style exactly. Examples from their format:
- dumbbell shoulder press - 75 * 6, 5, 4
- bicep curl - 55 * 7, 60 * 4, 2; 55 * 1
- pull-up - 0 * 15, 8, 8
- smith squats - 195 * 10, 10, 10

Guidelines:
- CRITICAL: Suggest exactly {max_exercises_to_generate} exercises (generate a full list - user will select how many to show)
- CRITICAL: Prioritize exercises that haven't been done in 7+ days (check Individual Exercise Tracking above)
- AVOID exercises done in the last 7 days - they need recovery
- For each exercise you suggest, check when it was last done - only suggest if it's been at least 3-4 days
- WEIGHT/REP SUGGESTIONS (CRITICAL - use progressive overload principles):
  * For each exercise, check the "Individual Exercise Tracking" section above to see last performance
  * Follow the progression guidelines shown for each exercise (7-14 days = progressive overload, 14-30 days = match, 30+ days = slightly lighter)
  * Progressive overload means: EITHER increase reps at same weight (+1 rep) OR increase weight by 2.5% max
  * IMPORTANT: Rep range for hypertrophy is 6-12 reps. NEVER suggest more than 12 reps
  * If last reps < 10: suggest same weight +1 rep (preferred, but cap at 12 reps max)
  * If last reps >= 10: suggest +2.5% weight same reps (don't increase reps above 12 - increase weight instead)
  * If last reps = 12: must increase weight by 2.5% max, keep reps at 12
  * The "last performance" shows reps from the HEAVIEST set, not from drop sets or warm-ups
  * If no history exists for an exercise, suggest a reasonable starting weight based on similar exercises in their history
- Prioritize compound movements when possible
- Exercise format: Show only ONE set per exercise (e.g., "dumbbell bench - 90 * 6"), NOT all sets like "90 * 6, 6, 6". User will fill in remaining sets themselves. This is a suggestion, not a complete workout log.
- Keep total suggestion brief and focused on exercises only
- Match the user's writing style and format exactly

5-word summary (NO brackets, just plain text):"""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,  # Increased to support 15 exercises
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        suggestion = message.content[0].text.strip()
        
        # Run evals on the suggestion (optional, for debugging/improvement)
        eval_results = None
        if os.getenv("RUN_EVALS", "false").lower() == "true":
            try:
                from evals import run_evals
                eval_results = run_evals(suggestion, workout_history[:1000])
            except Exception as e:
                # Evals are optional, don't fail if they don't work
                pass
        
        # Get budget info for display (but don't block)
        budget = check_budget()
        response = {
            'success': True,
            'suggestion': suggestion,
            'usage': {
                'cost': calculate_cost(input_tokens, output_tokens),
                'daily_cost': budget["daily_cost"]
            }
        }
        
        # Include eval results if available (for debugging)
        if eval_results:
            response['evals'] = {
                'overall_score': eval_results['overall_score'],
                'passed': eval_results['overall_passed']
            }
        
        return jsonify(response)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete-workout', methods=['POST'])
def delete_workout():
    """Delete a workout entry from database or file"""
    data = request.json
    workout_date = data.get('workout_date', '')
    workout_text = data.get('workout_text', '')
    
    if not all([workout_date, workout_text]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Try database first
    if delete_workout_from_db(workout_date, workout_text):
        return jsonify({'success': True, 'message': 'Workout deleted'})
    
    # Fall back to file-based
    deleted = False
    
    # Try to delete from workouts.md
    if WORKOUT_LOG.exists():
        content = WORKOUT_LOG.read_text()
        
        # Normalize the date - remove time if present for matching
        # The workout_date might be "11/11/25 2:30 PM" but in file it might be "11/11/25"
        date_without_time = workout_date.split()[0] if ' ' in workout_date else workout_date
        
        # Try multiple patterns to match (handle different whitespace and date formats)
        patterns = [
            f"{workout_date}\n\n{workout_text}",  # Full date with time
            f"{workout_date}\n\n{workout_text}\n",
            f"{workout_date}\n{workout_text}",
            f"{workout_date}\n{workout_text}\n",
            f"{date_without_time}\n\n{workout_text}",  # Date without time
            f"{date_without_time}\n\n{workout_text}\n",
            f"{date_without_time}\n{workout_text}",
            f"{date_without_time}\n{workout_text}\n",
        ]
        
        # Also try line-by-line matching for more robust deletion
        lines = content.split('\n')
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # Check if this line matches the date (with or without time)
            if line.strip() == workout_date.strip() or line.strip() == date_without_time.strip():
                # Check if the next non-empty lines match the workout text
                workout_lines = []
                j = i + 1
                # Skip empty lines after date
                while j < len(lines) and not lines[j].strip():
                    j += 1
                # Collect workout text lines until we hit another date or end
                while j < len(lines):
                    next_line = lines[j].strip()
                    # Stop if we hit another date
                    if next_line and re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}', next_line):
                        break
                    if next_line:
                        workout_lines.append(lines[j])
                    j += 1
                
                workout_text_found = '\n'.join(workout_lines).strip()
                if workout_text_found == workout_text.strip():
                    # Match found - skip this date and workout text
                    i = j
                    deleted = True
                    continue
            
            new_lines.append(line)
            i += 1
        
        if deleted:
            content = '\n'.join(new_lines)
        else:
            # Fallback to original pattern matching
            for pattern in patterns:
                if pattern in content:
                    content = content.replace(pattern, '', 1)
                    deleted = True
                    break
        
        if deleted:
            # Clean up extra newlines
            while '\n\n\n\n' in content:
                content = content.replace('\n\n\n\n', '\n\n')
            content = content.strip()
            if content:
                WORKOUT_LOG.write_text(content + '\n')
            else:
                WORKOUT_LOG.write_text('')
            
            # Rebuild rule-based categories (fast, no AI) and trigger AI rebuild in background
            try:
                workouts_after = parse_workout_entries(content) if content else []
                index = load_search_index()
                
                if index and '_metadata' in index:
                    # Rebuild rule-based categories from scratch (fast)
                    from workout_parser import parse_workout_text, normalize_exercise_name
                    themes = load_themes()
                    
                    pr_indices = []
                    chest_indices = []
                    full_body_indices = []
                    
                    for i, workout in enumerate(workouts_after):
                        workout_key = get_workout_key(workout.get('date', ''), workout.get('text', ''))
                        workout['theme'] = themes.get(workout_key, None)
                        
                        # Check PR (simplified - compare to previous workouts in list)
                        has_pr = False
                        parsed = parse_workout_text(workout.get('text', ''))
                        exercises = parsed.get('exercises', [])
                        
                        if exercises:
                            # Simple PR check: compare to workouts before this one
                            history_before = {}
                            for prev_workout in workouts_after[:i]:
                                prev_parsed = parse_workout_text(prev_workout.get('text', ''))
                                for ex in prev_parsed.get('exercises', []):
                                    ex_key = ex['exercise'].lower().strip()
                                    max_weight = ex.get('max_weight', 0)
                                    first_reps = ex.get('first_reps', 0)
                                    
                                    if ex_key not in history_before:
                                        history_before[ex_key] = {
                                            'best_weight': max_weight,
                                            'best_reps': first_reps
                                        }
                                    else:
                                        if max_weight > history_before[ex_key]['best_weight']:
                                            history_before[ex_key]['best_weight'] = max_weight
                                        if first_reps > history_before[ex_key]['best_reps']:
                                            history_before[ex_key]['best_reps'] = first_reps
                            
                            for ex in exercises:
                                ex_key = ex['exercise'].lower().strip()
                                current_weight = ex.get('max_weight', 0)
                                current_reps = ex.get('first_reps', 0)
                                is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                                
                                if ex_key in history_before:
                                    hist = history_before[ex_key]
                                    if is_bodyweight:
                                        if current_reps > hist['best_reps']:
                                            has_pr = True
                                            break
                                    else:
                                        if current_weight > hist['best_weight']:
                                            has_pr = True
                                            break
                            
                            if has_pr:
                                pr_indices.append(i)
                            
                            # Check muscle groups
                            muscle_groups = set()
                            has_chest = False
                            for ex in exercises:
                                ex_name = ex['exercise']
                                normalized_name, mapped_groups = normalize_exercise_name(ex_name)
                                muscle_groups.update(mapped_groups)
                                if 'chest' in mapped_groups:
                                    has_chest = True
                            
                            if has_chest:
                                chest_indices.append(i)
                            
                            if len(muscle_groups) >= 3:
                                full_body_indices.append(i)
                    
                    # Update rule-based categories
                    index['PR personal record'] = pr_indices[:20]
                    index['chest workout'] = chest_indices[:20]
                    index['full body'] = full_body_indices[:20]
                    
                    # Update metadata
                    index['_metadata']['workout_hash'] = get_workout_hash()
                    index['_metadata']['workout_count'] = len(workouts_after)
                    index['_metadata']['last_updated'] = datetime.now().isoformat()
                    
                    save_search_index(index)
                    
                    # Trigger background rebuild for AI categories
                    rebuild_ai_index_async()
            except Exception as e:
                print(f"Error updating search index on delete: {e}")
                rebuild_ai_index_async()
    
    
    # Delete theme if it exists
    themes = load_themes()
    workout_key = get_workout_key(workout_date, workout_text)
    if workout_key in themes:
        del themes[workout_key]
        save_themes(themes)
    
    if deleted:
        return jsonify({'success': True, 'message': 'Workout deleted'})
    else:
        return jsonify({'error': 'Workout entry not found'}), 404

@app.route('/api/ai-insights', methods=['POST'])
def get_ai_insights():
    """Get AI insights about overall progress"""
    # Check budget
    budget = check_budget()
    if budget["over_daily_budget"] or budget["over_monthly_budget"]:
        return jsonify({
            'error': 'Budget exceeded. Please check your usage.'
        }), 429
    
    context = load_user_context()
    workout_history = context.get('workout_history', '')
    
    prompt = f"""Analyze this workout history and provide insights about progress, patterns, and suggestions.

Workout history:
{workout_history[-10000:]}

Provide:
1. Progress highlights (what's improving)
2. Patterns you notice (frequency, consistency)
3. Potential plateaus or areas to focus on
4. 2-3 actionable suggestions

Keep it concise and encouraging (under 300 words)."""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        insights = message.content[0].text
        
        return jsonify({
            'success': True,
            'insights': insights,
            'usage': {
                'cost': calculate_cost(input_tokens, output_tokens),
                'daily_cost': budget["daily_cost"]
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics', methods=['GET'])
@require_auth
def get_analytics():
    """Get comprehensive analytics: strength trends, consistency, plateaus, muscle group balance"""
    from workout_parser import parse_workout_text, extract_muscle_groups_from_exercises
    from datetime import datetime, timedelta
    from collections import defaultdict
    
    # Load workouts for current user only
    user_id = get_current_user_id()
    workouts = get_workouts_from_db(user_id, limit=60) or []
    
    if not workouts:
        return jsonify({
            'success': True,
            'analytics': {
                'strength_trends': {'insights': [], 'summary': 'Not enough data yet'},
                'consistency': {'score': 0, 'streak': 0, 'insight': 'Log more workouts to see consistency metrics'},
                'plateaus': {'exercises': [], 'insight': 'No plateaus detected'},
                'muscle_balance': {'imbalances': [], 'insight': 'Not enough data yet'}
            }
        })
    
    today = datetime.now()
    
    # ===== 1. STRENGTH TREND INSIGHTS =====
    # Track exercise progress over last 30 days vs previous 30 days
    exercise_trends = defaultdict(list)
    
    for workout in workouts[:60]:  # Last 60 workouts
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 60:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            max_weight = ex.get('max_weight', 0)
            first_reps = ex.get('first_reps', 0)
            days_ago = (today - workout_date).days
            
            exercise_trends[ex_key].append({
                'date': workout_date,
                'days_ago': days_ago,
                'max_weight': max_weight,
                'first_reps': first_reps,
                'is_bodyweight': ex.get('is_bodyweight', False)
            })
    
    # Calculate trends for exercises with 3+ data points
    strength_trends = []
    for ex_key, data_points in exercise_trends.items():
        if len(data_points) < 3:
            continue
        
        # Sort by date (oldest first)
        data_points.sort(key=lambda x: x['date'])
        
        # Compare first half vs second half
        mid = len(data_points) // 2
        first_half = data_points[:mid]
        second_half = data_points[mid:]
        
        # Calculate average performance
        if not data_points[0]['is_bodyweight']:
            # Weighted exercises: track max weight
            first_avg_weight = sum(d['max_weight'] for d in first_half) / len(first_half)
            second_avg_weight = sum(d['max_weight'] for d in second_half) / len(second_half)
            
            if second_avg_weight > first_avg_weight * 1.05:  # 5%+ increase
                strength_trends.append({
                    'exercise': ex_key,
                    'improvement': f"+{second_avg_weight - first_avg_weight:.1f}lbs",
                    'percent': f"+{(second_avg_weight / first_avg_weight - 1) * 100:.0f}%"
                })
        else:
            # Bodyweight: track reps
            first_avg_reps = sum(d['first_reps'] for d in first_half) / len(first_half)
            second_avg_reps = sum(d['first_reps'] for d in second_half) / len(second_half)
            
            if second_avg_reps > first_avg_reps * 1.1:  # 10%+ increase
                strength_trends.append({
                    'exercise': ex_key,
                    'improvement': f"+{second_avg_reps - first_avg_reps:.0f} reps",
                    'percent': f"+{(second_avg_reps / first_avg_reps - 1) * 100:.0f}%"
                })
    
    # ===== 2. CONSISTENCY SCORE =====
    # Calculate workouts per week and current streak
    workout_dates = []
    for workout in workouts[:100]:
        workout_date_str = workout.get('date', '')
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 90:
                        continue
                    workout_dates.append(parsed_date.date())
                    break
                except:
                    continue
    
    workout_dates = sorted(set(workout_dates), reverse=True)  # Unique dates, newest first
    
    # Calculate current streak
    current_streak = 0
    if workout_dates:
        check_date = today.date()
        for i, date in enumerate(workout_dates):
            # workout_dates already contains date objects (from .date() call above)
            if date == check_date or date == check_date - timedelta(days=1):
                current_streak += 1
                check_date = date - timedelta(days=1)
            else:
                break
    
    # Calculate workouts per week (last 4 weeks)
    four_weeks_ago = today.date() - timedelta(days=28)
    recent_workouts = [d for d in workout_dates if d >= four_weeks_ago]
    workouts_per_week = len(recent_workouts) / 4.0 if recent_workouts else 0
    
    # ===== 3. PLATEAU DETECTION =====
    # Find exercises that haven't improved in 3+ weeks
    plateaus = []
    for ex_key, data_points in exercise_trends.items():
        if len(data_points) < 4:
            continue
        
        # Sort by date (newest first)
        data_points.sort(key=lambda x: x['date'], reverse=True)
        
        # Get most recent performance
        most_recent = data_points[0]
        days_since_last = most_recent['days_ago']
        
        if days_since_last > 21:  # Haven't done in 3+ weeks, skip
            continue
        
        # Check if performance has stagnated (last 3+ workouts show no improvement)
        if len(data_points) >= 3:
            recent_3 = data_points[:3]
            if not recent_3[0]['is_bodyweight']:
                # Weighted: check if max weight hasn't increased
                max_recent_weight = max(d['max_weight'] for d in recent_3)
                older_data = data_points[3:6] if len(data_points) >= 6 else data_points[3:]
                if older_data:
                    max_older_weight = max(d['max_weight'] for d in older_data)
                    if max_recent_weight <= max_older_weight:
                        plateaus.append({
                            'exercise': ex_key,
                            'current': f"{max_recent_weight}lbs",
                            'weeks_stagnant': len(recent_3)
                        })
            else:
                # Bodyweight: check if reps haven't increased
                max_recent_reps = max(d['first_reps'] for d in recent_3)
                older_data = data_points[3:6] if len(data_points) >= 6 else data_points[3:]
                if older_data:
                    max_older_reps = max(d['first_reps'] for d in older_data)
                    if max_recent_reps <= max_older_reps:
                        plateaus.append({
                            'exercise': ex_key,
                            'current': f"{max_recent_reps} reps",
                            'weeks_stagnant': len(recent_3)
                        })
    
    # ===== 4. MUSCLE GROUP BALANCE =====
    # Track training frequency per muscle group (last 30 days)
    muscle_group_counts = defaultdict(int)
    muscle_group_names = defaultdict(set)
    
    for workout in workouts[:40]:  # Last 40 workouts
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 30:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        muscle_groups = extract_muscle_groups_from_exercises(parsed_workout.get('exercises', []))
        
        # Also infer glutes, calves, abs
        exercise_names = [ex['exercise'].lower() for ex in parsed_workout.get('exercises', [])]
        for ex_name in exercise_names:
            if any(word in ex_name for word in ['squat', 'lunge', 'split', 'hip', 'glute']):
                muscle_groups.append('glutes')
            if 'calf' in ex_name:
                muscle_groups.append('calves')
            if any(word in ex_name for word in ['crunch', 'sit-up', 'plank', 'ab', 'core']):
                muscle_groups.append('abs')
        
        for group in set(muscle_groups):  # Count each group once per workout
            muscle_group_counts[group] += 1
    
    # Find imbalances (groups trained 2x+ more than others)
    if muscle_group_counts:
        max_count = max(muscle_group_counts.values())
        min_count = min(muscle_group_counts.values())
        imbalances = []
        
        for group, count in muscle_group_counts.items():
            if max_count > 0 and count < max_count * 0.4:  # Trained less than 40% of most-trained group
                imbalances.append({
                    'group': group,
                    'count': count,
                    'vs_max': max_count
                })
    
    # ===== GENERATE AI INSIGHTS =====
    # Build context for AI
    analytics_context = {
        'strength_trends': strength_trends[:5],  # Top 5
        'consistency': {
            'workouts_per_week': round(workouts_per_week, 1),
            'current_streak': current_streak
        },
        'plateaus': plateaus[:3],  # Top 3
        'muscle_imbalances': imbalances[:3] if imbalances else []  # Top 3
    }
    
    # Generate AI insights for each category
    insights = {}
    
    # Strength Trends Insight
    if strength_trends:
        trend_text = ", ".join([f"{t['exercise']} ({t['improvement']})" for t in strength_trends[:3]])
        prompt = f"""Based on this strength progress data, generate a brief, encouraging insight (1-2 sentences):

{trend_text}

Keep it concise and motivating. Example: Your bench press has increased 10lbs over the past month. Keep it up!"""
    else:
        insights['strength_trends'] = "Track more workouts to see strength trends"
        trend_text = ""
    
    # Consistency Insight
    consistency_text = f"Workouts per week: {workouts_per_week:.1f}, Current streak: {current_streak} days"
    consistency_prompt = f"""Based on this consistency data, generate a brief, encouraging insight (1-2 sentences):

{consistency_text}

Keep it concise and motivating. Example: You've worked out 4x/week for 3 weeks straight. That's consistency!"""
    
    # Plateau Insight
    if plateaus:
        plateau_text = ", ".join([f"{p['exercise']} (stagnant at {p['current']})" for p in plateaus[:2]])
        plateau_prompt = f"""Based on this plateau data, generate a brief, actionable suggestion (1-2 sentences):

{plateau_text}

Keep it concise and helpful. Example: Your squat has plateaued. Consider deloading or changing rep ranges."""
    else:
        insights['plateaus'] = "No plateaus detected - keep pushing!"
        plateau_text = ""
    
    # Muscle Balance Insight
    if imbalances:
        imbalance_text = ", ".join([f"{i['group']} ({i['count']}x vs {i['vs_max']}x)" for i in imbalances[:2]])
        balance_prompt = f"""Based on this muscle group training frequency, generate a brief, actionable suggestion (1-2 sentences):

{imbalance_text}

Keep it concise and helpful. Example: You've trained chest 8x this month but only hit legs 3x. Consider balancing."""
    else:
        insights['muscle_balance'] = "Your muscle group training is well-balanced!"
        imbalance_text = ""
    
    # Make AI calls (only if we have data)
    try:
        if strength_trends:
            message = anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            insights['strength_trends'] = message.content[0].text.strip()
            update_usage(message.usage.input_tokens, message.usage.output_tokens)
        
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": consistency_prompt}]
        )
        insights['consistency'] = message.content[0].text.strip()
        update_usage(message.usage.input_tokens, message.usage.output_tokens)
        
        if plateau_text:
            message = anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": plateau_prompt}]
            )
            insights['plateaus'] = message.content[0].text.strip()
            update_usage(message.usage.input_tokens, message.usage.output_tokens)
        
        if imbalance_text:
            message = anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": balance_prompt}]
            )
            insights['muscle_balance'] = message.content[0].text.strip()
            update_usage(message.usage.input_tokens, message.usage.output_tokens)
    except Exception as e:
        # Fallback to rule-based insights
        if not insights.get('strength_trends'):
            insights['strength_trends'] = f"Showing progress on {len(strength_trends)} exercises" if strength_trends else "Track more workouts to see trends"
        if not insights.get('consistency'):
            insights['consistency'] = f"You're averaging {workouts_per_week:.1f} workouts/week with a {current_streak}-day streak"
        if not insights.get('plateaus'):
            insights['plateaus'] = f"{len(plateaus)} exercises may need attention" if plateaus else "No plateaus detected"
        if not insights.get('muscle_balance'):
            insights['muscle_balance'] = f"{len(imbalances)} muscle groups need more attention" if imbalances else "Training is well-balanced"
    
    return jsonify({
        'success': True,
        'analytics': {
            'strength_trends': {
                'exercises': strength_trends[:5],
                'insight': insights.get('strength_trends', 'Track more workouts to see trends')
            },
            'consistency': {
                'workouts_per_week': round(workouts_per_week, 1),
                'current_streak': current_streak,
                'insight': insights.get('consistency', 'Keep logging workouts')
            },
            'plateaus': {
                'exercises': plateaus[:3],
                'insight': insights.get('plateaus', 'No plateaus detected')
            },
            'muscle_balance': {
                'imbalances': imbalances[:3] if imbalances else [],
                'insight': insights.get('muscle_balance', 'Training is well-balanced')
            }
        }
    })

@app.route('/api/generate-neglected-workout', methods=['GET'])
def generate_neglected_workout():
    """Generate a workout targeting neglected or ready-to-train muscle groups (rule-based)"""
    from workout_parser import parse_workout_text, extract_muscle_groups_from_exercises, normalize_exercise_name, load_exercise_mapping
    from datetime import datetime
    import json
    
    # Get user-specific workouts - require authentication
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Load exercise mapping
    try:
        exercise_mapping = load_exercise_mapping()
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error loading exercise mapping: {str(e)}'
        }), 500
    
    # Get workouts for current user only from database
    workouts = get_workouts_from_db(user_id, limit=30) or []
    
    # Track muscle group training dates (same logic as recovery check)
    muscle_group_last_trained = {}
    today = datetime.now()
    
    for workout in workouts[:20]:
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 14:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        muscle_groups = extract_muscle_groups_from_exercises(parsed_workout.get('exercises', []))
        
        # Also infer glutes, calves, abs
        exercise_names = [ex['exercise'].lower() for ex in parsed_workout.get('exercises', [])]
        for ex_name in exercise_names:
            if any(word in ex_name for word in ['squat', 'lunge', 'split', 'hip', 'glute']):
                muscle_groups.append('glutes')
            if 'calf' in ex_name:
                muscle_groups.append('calves')
            if any(word in ex_name for word in ['crunch', 'sit-up', 'plank', 'ab', 'core']):
                muscle_groups.append('abs')
        
        days_ago = (today - workout_date).days
        for group in set(muscle_groups):
            if group not in muscle_group_last_trained or days_ago < muscle_group_last_trained[group]:
                muscle_group_last_trained[group] = days_ago
    
    # Find neglected groups (7+ days or never trained) OR ready-to-train groups (4-6 days)
    all_groups = ['chest', 'back', 'shoulders', 'arms', 'biceps', 'triceps', 'legs', 'glutes', 'calves', 'core', 'abs']
    target_groups_list = []
    for group in all_groups:
        if group not in muscle_group_last_trained:
            target_groups_list.append((group, None, 'neglected'))
        elif muscle_group_last_trained[group] >= 7:
            target_groups_list.append((group, muscle_group_last_trained[group], 'neglected'))
        elif muscle_group_last_trained[group] >= 4:  # Ready to train (4-6 days)
            target_groups_list.append((group, muscle_group_last_trained[group], 'ready'))
    
    # Remove duplicates (prioritize specific over general)
    filtered_targets = []
    for group, days, status in target_groups_list:
        if group == 'arms' and (('biceps' in [g[0] for g in target_groups_list] or 'triceps' in [g[0] for g in target_groups_list])):
            continue
        filtered_targets.append((group, days, status))
    
    # Prioritize neglected over ready-to-train, then by days (most neglected first)
    filtered_targets.sort(key=lambda x: (0 if x[2] == 'neglected' else 1, x[1] if x[1] is not None else 999))
    # Take top 5-6 groups to ensure we can build a full workout
    target_groups_list = filtered_targets[:6]  # Top 6 groups for full workout
    
    if not target_groups_list:
        return jsonify({
            'success': True,
            'workout': '',
            'message': 'No target muscle groups found'
        })
    
    # Find exercises that target these groups
    target_groups = [g[0] for g in target_groups_list]
    
    # First, try to find recent workouts that targeted these groups
    # Look at last 20 workouts and find ones that hit our target groups
    matching_workouts = []
    for workout in workouts[:20]:
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 30:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if not workout_date:
            continue
        
        parsed_workout = parse_workout_text(workout.get('text', ''))
        muscle_groups = extract_muscle_groups_from_exercises(parsed_workout.get('exercises', []))
        
        # Also infer glutes, calves, abs
        exercise_names = [ex['exercise'].lower() for ex in parsed_workout.get('exercises', [])]
        for ex_name in exercise_names:
            if any(word in ex_name for word in ['squat', 'lunge', 'split', 'hip', 'glute']):
                muscle_groups.append('glutes')
            if 'calf' in ex_name:
                muscle_groups.append('calves')
            if any(word in ex_name for word in ['crunch', 'sit-up', 'plank', 'ab', 'core']):
                muscle_groups.append('abs')
        
        # Check if this workout targets any of our target groups
        workout_groups = set(muscle_groups)
        if any(mg in target_groups for mg in workout_groups):
            matching_workouts.append({
                'workout': parsed_workout,
                'date': workout_date,
                'exercises': parsed_workout.get('exercises', []),
                'groups': workout_groups
            })
    
    # Get user's historical weights/reps for exercises
    exercise_history = {}
    for workout in workouts[:30]:  # Last 30 workouts
        parsed_workout = parse_workout_text(workout.get('text', ''))
        for ex in parsed_workout.get('exercises', []):
            ex_key = ex['exercise'].lower().strip()
            normalized, _ = normalize_exercise_name(ex_key)
            if normalized not in exercise_history:
                exercise_history[normalized] = {
                    'max_weight': ex.get('max_weight', 0),
                    'first_reps': ex.get('first_reps', 0),
                    'is_bodyweight': ex.get('is_bodyweight', False)
                }
            else:
                if ex.get('max_weight', 0) > exercise_history[normalized]['max_weight']:
                    exercise_history[normalized]['max_weight'] = ex.get('max_weight', 0)
                    exercise_history[normalized]['first_reps'] = ex.get('first_reps', 0)
    
    # If we found matching workouts, extract exercises from them
    # Otherwise, fall back to exercise mapping
    suggested_exercises = []
    
    if matching_workouts:
        # Use exercises from recent matching workouts
        # Prioritize most recent workouts
        matching_workouts.sort(key=lambda x: x['date'], reverse=True)
        
        # Collect exercises from matching workouts
        for workout_data in matching_workouts[:3]:  # Look at top 3 matching workouts
            for ex in workout_data['exercises']:
                ex_name = ex['exercise']
                normalized, _ = normalize_exercise_name(ex_name)
                
                # Check if this exercise targets our groups
                ex_groups = extract_muscle_groups_from_exercises([ex])
                if any(mg in target_groups for mg in ex_groups):
                    # Check if we already have this exercise
                    if not any(e['name'].lower() == normalized.lower() for e in suggested_exercises):
                        suggested_exercises.append({
                            'name': ex_name,  # Keep original name from workout
                            'normalized': normalized,
                            'groups': ex_groups,
                            'max_weight': ex.get('max_weight', 0),
                            'first_reps': ex.get('first_reps', 0),
                            'is_bodyweight': ex.get('is_bodyweight', False),
                            'is_compound': len(ex_groups) > 1 or any(word in ex_name.lower() for word in ['squat', 'bench', 'press', 'row', 'deadlift', 'pull-up', 'dip'])
                        })
    else:
        # Fall back to exercise mapping
        if not exercise_mapping or 'mappings' not in exercise_mapping:
            return jsonify({
                'success': False,
                'error': 'Exercise mapping not found'
            }), 500
        
        for ex_key, ex_data in exercise_mapping.get('mappings', {}).items():
            muscle_groups = ex_data.get('muscle_groups', [])
            if any(mg in target_groups for mg in muscle_groups):
                normalized = ex_data.get('normalized', ex_key)
                hist = exercise_history.get(normalized.lower(), {})
                suggested_exercises.append({
                    'name': normalized,
                    'normalized': normalized,
                    'groups': muscle_groups,
                    'max_weight': hist.get('max_weight', 0),
                    'first_reps': hist.get('first_reps', 0),
                    'is_bodyweight': hist.get('is_bodyweight', False),
                    'is_compound': len(muscle_groups) > 1 or any(word in normalized.lower() for word in ['squat', 'bench', 'press', 'row', 'deadlift', 'pull-up', 'dip'])
                })
    
    # Sort exercises: compound/heavy first (compound exercises, then by weight)
    # Compound exercises are those that target multiple muscle groups or are heavy compound movements
    suggested_exercises.sort(key=lambda x: (
        not x.get('is_compound', False),  # Compound exercises first (False sorts before True)
        -x.get('max_weight', 0)  # Then by weight (heaviest first)
    ))
    
    # Build workout (5-6 exercises) - prioritize covering all target groups
    workout_lines = []
    used_groups = set()
    selected_exercises = set()  # Avoid duplicates
    
    # First pass: prioritize exercises that target multiple neglected groups
    for ex in suggested_exercises:
        if len(workout_lines) >= 6:
            break
        
        # Check if this exercise targets groups we haven't covered yet
        targets_uncovered = [mg for mg in ex['groups'] if mg in target_groups and mg not in used_groups]
        if not targets_uncovered:
            continue
        
        ex_name = ex['name']
        # Avoid duplicate exercises
        if ex_name.lower() in selected_exercises:
            continue
        
        # Use the exercise data we already have (from matching workouts or history)
        max_weight = ex.get('max_weight', 0)
        first_reps = ex.get('first_reps', 0)
        is_bodyweight = ex.get('is_bodyweight', False)
        
        if is_bodyweight or max_weight == 0:
            # Bodyweight exercise - suggest reps only
            suggested_reps = first_reps if first_reps > 0 else 10
            workout_lines.append(f"{ex_name} - 0 * {suggested_reps}")
        else:
            # Weighted exercise - suggest weight and reps
            suggested_weight = max_weight
            suggested_reps = first_reps if first_reps > 0 else 6
            if suggested_weight == 0:
                # No history, use a default based on exercise type
                if 'bench' in ex_name.lower() or 'press' in ex_name.lower():
                    suggested_weight = 50
                elif 'curl' in ex_name.lower():
                    suggested_weight = 30
                elif 'squat' in ex_name.lower() or 'leg' in ex_name.lower():
                    suggested_weight = 100
                else:
                    suggested_weight = 40
            
            workout_lines.append(f"{ex_name} - {suggested_weight} * {suggested_reps}")
        
        # Mark groups as covered
        for mg in targets_uncovered:
            used_groups.add(mg)
        
        selected_exercises.add(ex_name.lower())
    
    # Second pass: if we don't have enough exercises, add more to cover remaining groups
    if len(workout_lines) < 5:
        for ex in suggested_exercises:
            if len(workout_lines) >= 6:
                break
            
            # Check if this exercise targets any remaining groups
            targets_uncovered = [mg for mg in ex['groups'] if mg in target_groups and mg not in used_groups]
            if not targets_uncovered:
                # Still add if we need more exercises (even if groups are covered)
                if len(workout_lines) < 5:
                    targets_uncovered = [mg for mg in ex['groups'] if mg in target_groups]
                    if not targets_uncovered:
                        continue
            
            ex_name = ex['name']
            # Avoid duplicate exercises
            if ex_name.lower() in selected_exercises:
                continue
            
            # Use the exercise data we already have
            max_weight = ex.get('max_weight', 0)
            first_reps = ex.get('first_reps', 0)
            is_bodyweight = ex.get('is_bodyweight', False)
            
            if is_bodyweight or max_weight == 0:
                suggested_reps = first_reps if first_reps > 0 else 10
                workout_lines.append(f"{ex_name} - 0 * {suggested_reps}")
            else:
                suggested_weight = max_weight
                suggested_reps = first_reps if first_reps > 0 else 6
                if suggested_weight == 0:
                    if 'bench' in ex_name.lower() or 'press' in ex_name.lower():
                        suggested_weight = 50
                    elif 'curl' in ex_name.lower():
                        suggested_weight = 30
                    elif 'squat' in ex_name.lower() or 'leg' in ex_name.lower():
                        suggested_weight = 100
                    else:
                        suggested_weight = 40
                
                workout_lines.append(f"{ex_name} - {suggested_weight} * {suggested_reps}")
            
            # Mark groups as covered
            for mg in targets_uncovered:
                used_groups.add(mg)
            
            selected_exercises.add(ex_name.lower())
    
    if not workout_lines:
        return jsonify({
            'success': True,
            'workout': '',
            'message': 'Could not generate workout for neglected groups'
        })
    
    workout_text = '\n'.join(workout_lines)
    
    return jsonify({
        'success': True,
        'workout': workout_text,
        'neglected_groups': target_groups
    })

@app.route('/api/search-workouts', methods=['POST'])
@require_auth
def search_workouts():
    """Search workouts - uses cached index for presets, AI for free-form"""
    data = request.json
    query = data.get('query', '').strip()
    
    if not query:
        return jsonify({
            'success': True,
            'workout_indices': []
        })
    
    # Check if this is a preset query (use fast index lookup)
    preset_queries = ['chest workout', 'leg day', 'upper body', 'PR personal record', 'full body']
    is_preset = query in preset_queries
    
    if is_preset:
        # Use cached search index for instant results
        index = ensure_search_index()
        if query in index:
            return jsonify({
                'success': True,
                'workout_indices': index[query]
            })
    
    # For free-form queries, use AI search (original implementation)
    # Load workouts for current user only
    user_id = get_current_user_id()
    workouts = get_workouts_from_db(user_id) or []
    
    if not workouts:
        return jsonify({
            'success': True,
            'workout_indices': []
        })
    
    # Load themes and detect PRs (same logic as get_workouts)
    themes = load_themes()
    from workout_parser import parse_workout_text
    from datetime import datetime
    today = datetime.now()
    
    # Detect PRs for workouts (same as get_workouts)
    for i, workout in enumerate(workouts):
        workout_key = get_workout_key(workout.get('date', ''), workout.get('text', ''))
        workout['theme'] = themes.get(workout_key, None)
        
        has_pr = False
        has_strength_increase = False
        
        workout_date_str = workout.get('date', '')
        workout_date = None
        if workout_date_str:
            for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(workout_date_str, fmt)
                    if parsed_date.year > today.year + 1 or (parsed_date - today).days > 1:
                        continue
                    workout_date = parsed_date
                    break
                except:
                    continue
        
        if workout_date:
            history_before = {}
            for prev_workout in workouts:
                prev_date_str = prev_workout.get('date', '')
                prev_date = None
                if prev_date_str:
                    for fmt in ['%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p', '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%y', '%m/%d/%Y']:
                        try:
                            prev_parsed = datetime.strptime(prev_date_str, fmt)
                            if prev_parsed.year > today.year + 1 or (prev_parsed - today).days > 1:
                                continue
                            prev_date = prev_parsed
                            break
                        except:
                            continue
                
                if prev_date and prev_date < workout_date:
                    prev_parsed_exercises = parse_workout_text(prev_workout.get('text', '')).get('exercises', [])
                    for ex in prev_parsed_exercises:
                        ex_key = ex['exercise'].lower().strip()
                        max_weight = ex.get('max_weight', 0)
                        first_reps = ex.get('first_reps', 0)
                        
                        if ex_key not in history_before:
                            history_before[ex_key] = {
                                'best_weight': max_weight,
                                'best_reps': first_reps,
                                'best_weight_reps': first_reps if max_weight > 0 else 0
                            }
                        else:
                            if max_weight > history_before[ex_key]['best_weight']:
                                history_before[ex_key]['best_weight'] = max_weight
                                history_before[ex_key]['best_weight_reps'] = first_reps
                            if first_reps > history_before[ex_key]['best_reps']:
                                history_before[ex_key]['best_reps'] = first_reps
            
            current_parsed_exercises = parse_workout_text(workout.get('text', '')).get('exercises', [])
            for ex in current_parsed_exercises:
                ex_key = ex['exercise'].lower().strip()
                current_weight = ex.get('max_weight', 0)
                current_reps = ex.get('first_reps', 0)
                is_bodyweight = ex.get('is_bodyweight', False) or current_weight == 0
                
                if ex_key in history_before:
                    hist = history_before[ex_key]
                    if is_bodyweight:
                        if current_reps > hist['best_reps']:
                            has_pr = True
                    else:
                        if current_weight > hist['best_weight']:
                            has_pr = True
                        elif current_weight == hist['best_weight'] and current_reps > hist['best_weight_reps']:
                            has_strength_increase = True
        
        workout['has_pr'] = has_pr
        workout['has_strength_increase'] = has_strength_increase
    
    # Build context for AI search
    # Include recent workouts (last 100) for search
    workout_context = []
    for i, workout in enumerate(workouts[:100]):
        workout_text = workout.get('text', '')[:200]  # First 200 chars
        theme = workout.get('theme', '')
        date = workout.get('date', '')
        # Include PR/strength increase flags
        pr_flag = "🏆 PR" if workout.get('has_pr', False) else ""
        strength_flag = "📈 Strength" if workout.get('has_strength_increase', False) else ""
        flags = f" {pr_flag} {strength_flag}".strip()
        workout_context.append(f"[{i}] {date} | {theme}{flags} | {workout_text}")
    
    context_text = '\n'.join(workout_context)
    
    # Use AI to find relevant workouts
    prompt = f"""You are searching through workout history. Find workouts that match this query semantically (meaning, not just keywords).

Query: "{query}"

Workout history (format: [index] date | theme | workout text):
{context_text}

Return ONLY a comma-separated list of indices (numbers in brackets) that match the query. For example: "0, 3, 7, 12"
If no workouts match, return an empty string.
Focus on semantic meaning:
- "chest workout" → find workouts with chest exercises (bench press, push-ups, etc.)
- "PR" or "personal record" → find workouts where the user hit a PR (look for exercises with higher weights/reps than previous workouts, or themes mentioning PRs)
- "leg day" → find workouts with leg exercises (squats, lunges, etc.)
- "upper body" → find workouts with upper body exercises (chest, back, shoulders, arms)
- "full body" → find workouts that target multiple muscle groups

Return at most 20 indices, prioritizing the most relevant matches."""

    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        result_text = message.content[0].text.strip()
        
        # Parse indices from response
        workout_indices = []
        if result_text:
            # Extract numbers from the response
            import re
            indices = re.findall(r'\b(\d+)\b', result_text)
            workout_indices = [int(idx) for idx in indices if int(idx) < len(workouts)]
        
        return jsonify({
            'success': True,
            'workout_indices': workout_indices
        })
        
    except Exception as e:
        # Fallback to keyword search
        workout_indices = []
        query_lower = query.lower()
        for i, workout in enumerate(workouts[:100]):
            workout_text = workout.get('text', '').lower()
            theme = workout.get('theme', '').lower()
            if query_lower in workout_text or query_lower in theme:
                workout_indices.append(i)
        
        return jsonify({
            'success': True,
            'workout_indices': workout_indices
        })

@app.route('/api/coach', methods=['POST'])
def coach():
    """Get AI coach response"""
    # Check budget first
    budget = check_budget()
    if budget["over_daily_budget"]:
        return jsonify({
            'error': f'Daily budget exceeded (${budget["daily_cost"]:.2f} / ${DAILY_BUDGET:.2f}). Please check your usage or increase DAILY_BUDGET in .env'
        }), 429
    if budget["over_monthly_budget"]:
        return jsonify({
            'error': f'Monthly budget exceeded (${budget["monthly_cost"]:.2f} / ${MONTHLY_BUDGET:.2f}). Please check your usage or increase MONTHLY_BUDGET in .env'
        }), 429
    
    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])
    
    # Load context
    context = load_user_context()
    workout_history = context.get('workout_history', '')
    
    # Build prompt for Claude - KEEP IT CONCISE to save tokens
    system_prompt = """You are a friendly, supportive AI fitness coach. You help users track workouts, understand their progress, and stay motivated.

Your style:
- Conversational and encouraging (like a good friend who's also a coach)
- Ask freeform questions naturally ("How did you feel today?" "How'd you sleep?")
- Detect plateaus and suggest next steps
- Celebrate wins and progress
- Keep responses concise and actionable (under 200 words)

You have access to the user's workout history. Use it to:
- Understand their patterns and progress
- Detect plateaus
- Suggest appropriate next workouts
- Ask relevant questions about recovery, sleep, energy

The user logs workouts in freeform text. You should understand their format and help them progress."""
    
    # Only include last 3 conversation turns to save tokens
    recent_convo = chr(10).join([f"User: {h.get('user', '')}\nCoach: {h.get('coach', '')}" for h in conversation_history[-3:]])
    
    user_prompt = f"""User's recent workout history:
{workout_history[-8000:]}

Recent conversation:
{recent_convo}

User: {user_message}

Coach:"""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=500,  # REDUCED from 1000 to save costs
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        daily_cost, total_cost = update_usage(input_tokens, output_tokens)
        
        response_text = message.content[0].text
        
        # Get updated budget
        budget = check_budget()
        
        return jsonify({
            'success': True,
            'response': response_text,
            'usage': {
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cost': calculate_cost(input_tokens, output_tokens),
                'daily_cost': budget["daily_cost"],
                'daily_remaining': budget["daily_remaining"],
                'total_cost': total_cost
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/suggest-questions', methods=['POST'])
def suggest_questions():
    """Get AI-suggested questions based on recent workouts"""
    # Check budget
    budget = check_budget()
    if budget["over_daily_budget"] or budget["over_monthly_budget"]:
        # Return default questions if over budget
        return jsonify({
            'success': True,
            'questions': ["How did your last workout feel?", "What are you working on today?", "How's your recovery?"]
        })
    
    context = load_user_context()
    workout_history = context.get('workout_history', '')
    
    prompt = f"""Based on this workout history, suggest 2-3 natural, freeform questions the coach should ask the user right now. 
Make them feel conversational, not like a form.

Workout history:
{workout_history[-3000:]}

Return just the questions, one per line, no numbering."""
    
    try:
        message = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,  # REDUCED to save costs
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Track usage
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        update_usage(input_tokens, output_tokens)
        
        questions = [q.strip() for q in message.content[0].text.split('\n') if q.strip()]
        
        return jsonify({
            'success': True,
            'questions': questions[:3]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/usage', methods=['GET'])
def get_usage():
    """Get usage statistics"""
    budget = check_budget()
    usage = load_usage()
    today = datetime.now().strftime("%Y-%m-%d")
    
    return jsonify({
        'success': True,
        'budget': budget,
        'today': usage["daily"].get(today, {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "requests": 0}),
        'total': usage["total"],
        'recent_days': {k: v for k, v in sorted(usage["daily"].items(), reverse=True)[:7]}  # Last 7 days
    })

@app.route('/api/submit-feedback', methods=['POST'])
def submit_suggestion_feedback():
    """Receive feedback on suggestions for debugging/improvement"""
    data = request.json
    suggestion = data.get('suggestion', '')
    feedback = data.get('feedback', '')
    timestamp = data.get('timestamp', datetime.now().isoformat())
    
    # Load existing feedback
    feedbacks = []
    if FEEDBACK_LOG.exists():
        try:
            feedbacks = json.loads(FEEDBACK_LOG.read_text())
        except:
            feedbacks = []
    
    # Add new feedback
    feedback_entry = {
        'timestamp': timestamp,
        'suggestion': suggestion,
        'feedback': feedback
    }
    feedbacks.append(feedback_entry)
    
    # Save feedback
    try:
        FEEDBACK_LOG.write_text(json.dumps(feedbacks, indent=2))
        print(f"\n📝 FEEDBACK RECEIVED:")
        print(f"Time: {timestamp}")
        print(f"Suggestion: {suggestion[:100]}...")
        print(f"Feedback: {feedback}")
        print("-" * 50)
        return jsonify({'success': True, 'message': 'Feedback received'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/view-feedback', methods=['GET'])
def view_feedback():
    """View all feedback (for debugging)"""
    if not FEEDBACK_LOG.exists():
        return jsonify({'feedbacks': []})
    
    try:
        feedbacks = json.loads(FEEDBACK_LOG.read_text())
        return jsonify({'feedbacks': feedbacks, 'count': len(feedbacks)})
    except:
        return jsonify({'feedbacks': [], 'count': 0})

if __name__ == '__main__':
    print("\n" + "="*50)
    print("AI Fitness Coach - Cost Management")
    print("="*50)
    budget = check_budget()
    print(f"Daily Budget: ${DAILY_BUDGET:.2f} (${budget['daily_remaining']:.2f} remaining)")
    print(f"Monthly Budget: ${MONTHLY_BUDGET:.2f} (${budget['monthly_remaining']:.2f} remaining)")
    print(f"Total Spent: ${budget['total_cost']:.2f}")
    print("="*50 + "\n")
    print("Starting server on http://localhost:5001")
    print(f"Also accessible on your network at: http://172.20.10.4:5001")
    print("Press Ctrl+C to stop\n")
    app.run(debug=True, host='0.0.0.0', port=5001)

