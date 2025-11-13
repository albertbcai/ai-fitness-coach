"""
Database module for AI Fitness Coach
Handles PostgreSQL and SQLite connections and schema
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Try to import psycopg2 for PostgreSQL support
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

def get_db_url():
    """Get database URL from environment variable"""
    # Railway provides DATABASE_URL, local dev can use POSTGRES_URL
    db_url = os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL')
    if not db_url:
        # Fallback to SQLite for local development
        return 'sqlite:///fitness_coach.db'
    return db_url

def is_sqlite(db_url):
    """Check if database URL is SQLite"""
    return db_url and db_url.startswith('sqlite:///')

def get_cursor(conn):
    """Get a cursor from connection - handles both SQLite and PostgreSQL"""
    db_url = get_db_url()
    if is_sqlite(db_url):
        # For SQLite, return a wrapper that makes the connection act like a cursor
        class SQLiteCursorWrapper:
            def __init__(self, conn):
                self.conn = conn
                self._last_cursor = None
            
            def execute(self, query, params=None):
                if params:
                    self._last_cursor = self.conn.execute(query, params)
                else:
                    self._last_cursor = self.conn.execute(query)
                return self._last_cursor
            
            def fetchone(self):
                if self._last_cursor:
                    return self._last_cursor.fetchone()
                return None
            
            def fetchall(self):
                if self._last_cursor:
                    return self._last_cursor.fetchall()
                return []
            
            @property
            def lastrowid(self):
                if self._last_cursor:
                    return self._last_cursor.lastrowid
                return self.conn.lastrowid if hasattr(self.conn, 'lastrowid') else None
            
            @property
            def rowcount(self):
                if self._last_cursor:
                    return self._last_cursor.rowcount
                return 0
        
        return SQLiteCursorWrapper(conn)
    else:
        return conn.cursor()

@contextmanager
def get_db_connection():
    """Get a database connection with automatic cleanup"""
    db_url = get_db_url()
    if not db_url:
        raise ValueError("No database URL found. Set DATABASE_URL or POSTGRES_URL environment variable.")
    
    # Check if it's SQLite
    if is_sqlite(db_url):
        db_path = db_url.replace('sqlite:///', '')
        # Make path absolute
        if not os.path.isabs(db_path):
            db_path = str(Path(__file__).parent / db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        # PostgreSQL
        if not HAS_POSTGRES:
            raise ValueError("PostgreSQL URL provided but psycopg2 not installed. Install with: pip install psycopg2-binary")
        
        # Handle Railway's postgres:// URL format (convert to postgresql://)
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        
        conn = psycopg2.connect(db_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def init_db():
    """Initialize database tables - works with both PostgreSQL and SQLite"""
    db_url = get_db_url()
    use_sqlite = is_sqlite(db_url)
    
    with get_db_connection() as conn:
        if use_sqlite:
            cur = conn
        else:
            cur = conn.cursor()
        
        # Users table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        
        # Create index on username for faster lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)
        """)
        
        # Workouts table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workouts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    date TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Check and add user_id column if it doesn't exist (for migration)
            try:
                cur.execute("PRAGMA table_info(workouts)")
                columns = [row[1] for row in cur.fetchall()]
                if 'user_id' not in columns:
                    cur.execute("ALTER TABLE workouts ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workouts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    date TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Add user_id column if table exists but column doesn't (PostgreSQL migration)
            try:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='workouts' AND column_name='user_id'
                """)
                if not cur.fetchone():
                    cur.execute("""
                        ALTER TABLE workouts 
                        ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
                    """)
            except:
                pass
        
        # Create indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_workouts_user_id ON workouts(user_id)
        """)
        
        # Themes table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    workout_key TEXT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    theme TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workout_key, user_id)
                )
            """)
            # Migration: add user_id if needed
            try:
                cur.execute("PRAGMA table_info(themes)")
                columns = [row[1] for row in cur.fetchall()]
                if 'user_id' not in columns:
                    # SQLite doesn't support dropping PK easily, so we'll recreate if needed
                    # For now, just add the column
                    cur.execute("ALTER TABLE themes ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    workout_key TEXT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    theme TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workout_key, user_id)
                )
            """)
            # Migration: add user_id if needed
            try:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='themes' AND column_name='user_id'
                """)
                if not cur.fetchone():
                    # Drop old PK if exists, add user_id, recreate PK
                    try:
                        cur.execute("ALTER TABLE themes DROP CONSTRAINT themes_pkey")
                    except:
                        pass
                    cur.execute("ALTER TABLE themes ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                    cur.execute("ALTER TABLE themes ADD PRIMARY KEY (workout_key, user_id)")
            except:
                pass
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_themes_user_id ON themes(user_id)
        """)
        
        # Usage tracking table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cost DECIMAL(10, 6) DEFAULT 0.0,
                    requests INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date)
                )
            """)
            # Migration
            try:
                cur.execute("PRAGMA table_info(usage)")
                columns = [row[1] for row in cur.fetchall()]
                if 'user_id' not in columns:
                    cur.execute("ALTER TABLE usage ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cost DECIMAL(10, 6) DEFAULT 0.0,
                    requests INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date)
                )
            """)
            # Migration
            try:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='usage' AND column_name='user_id'
                """)
                if not cur.fetchone():
                    try:
                        cur.execute("ALTER TABLE usage DROP CONSTRAINT usage_date_key")
                    except:
                        pass
                    cur.execute("ALTER TABLE usage ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                    cur.execute("ALTER TABLE usage ADD CONSTRAINT usage_user_date_unique UNIQUE(user_id, date)")
            except:
                pass
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_user_id ON usage(user_id)
        """)
        
        # Feedback table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    user_agent TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Migration
            try:
                cur.execute("PRAGMA table_info(feedback)")
                columns = [row[1] for row in cur.fetchall()]
                if 'user_id' not in columns:
                    cur.execute("ALTER TABLE feedback ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    user_agent TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Migration
            try:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='feedback' AND column_name='user_id'
                """)
                if not cur.fetchone():
                    cur.execute("ALTER TABLE feedback ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback(timestamp)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback(user_id)
        """)
        
        # Search index table
        if use_sqlite:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS search_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    workout_ids TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, category)
                )
            """)
            # Migration
            try:
                cur.execute("PRAGMA table_info(search_index)")
                columns = [row[1] for row in cur.fetchall()]
                if 'user_id' not in columns:
                    cur.execute("ALTER TABLE search_index ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            except:
                pass
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS search_index (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    workout_ids INTEGER[] NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, category)
                )
            """)
            # Migration
            try:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='search_index' AND column_name='user_id'
                """)
                if not cur.fetchone():
                    try:
                        cur.execute("ALTER TABLE search_index DROP CONSTRAINT search_index_category_key")
                    except:
                        pass
                    cur.execute("ALTER TABLE search_index ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                    cur.execute("ALTER TABLE search_index ADD CONSTRAINT search_index_user_category_unique UNIQUE(user_id, category)")
            except:
                pass
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_category ON search_index(category)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_user_id ON search_index(user_id)
        """)
        
        conn.commit()
        print("Database tables initialized successfully")

def check_db_connection():
    """Check if database connection works"""
    try:
        db_url = get_db_url()
        use_sqlite = is_sqlite(db_url)
        with get_db_connection() as conn:
            if use_sqlite:
                conn.execute("SELECT 1")
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False
