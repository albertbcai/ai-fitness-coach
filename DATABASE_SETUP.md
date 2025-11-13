# Database Setup Guide

This guide will help you set up PostgreSQL database for the AI Fitness Coach app on Railway.

## Step 1: Add PostgreSQL Database to Railway

1. Go to your Railway project: https://railway.com/project/[your-project-id]
2. Click **"+ New"** → **"Database"** → **"Add PostgreSQL"**
3. Railway will automatically create a PostgreSQL database
4. The `DATABASE_URL` environment variable will be automatically set

## Step 2: Verify Environment Variable

1. Go to your project **Settings** → **Variables**
2. Verify that `DATABASE_URL` is set (Railway sets this automatically)
3. The format should be: `postgresql://user:password@host:port/database`

## Step 3: Deploy the Updated Code

The code has been updated to:
- Automatically detect and use the database if `DATABASE_URL` is available
- Fall back to file storage if database is not available
- Initialize database tables on first startup

Just push your code to GitHub and Railway will automatically redeploy:

```bash
git add .
git commit -m "Add database support"
git push origin main
```

## Step 4: Migrate Existing Data (Optional)

If you have existing data in files (`workouts.md`, `themes.json`, etc.), you can migrate it to the database:

### Option A: Run Migration Locally (Recommended)

1. Set your local `DATABASE_URL` to point to Railway's database:
   ```bash
   export DATABASE_URL="postgresql://user:password@host:port/database"
   ```
   (Get this from Railway's database settings)

2. Run the migration script:
   ```bash
   python3 migrate_to_db.py
   ```

### Option B: Run Migration on Railway

1. SSH into your Railway service (if available)
2. Or create a one-time migration service:
   - Add a new service
   - Set command to: `python3 migrate_to_db.py`
   - Run it once, then delete the service

## Step 5: Verify It's Working

1. Check Railway logs - you should see:
   ```
   ✓ Database initialized
   ```

2. Add a test workout in the app

3. Check that data persists after a redeployment

## Troubleshooting

### Database not connecting?
- Check that `DATABASE_URL` is set in Railway variables
- Verify the database service is running in Railway
- Check Railway logs for connection errors

### Migration failed?
- Make sure database tables are initialized (app does this on startup)
- Check that your `DATABASE_URL` is correct
- Verify file permissions for reading local files

### App falls back to files?
- Check Railway logs for database connection errors
- Verify `DATABASE_URL` environment variable is set
- The app will automatically use files if database is unavailable (safe fallback)

## Data Storage

Once migrated, all data is stored in PostgreSQL:

- **workouts** table: All workout entries
- **themes** table: AI-generated workout themes
- **usage** table: API usage tracking
- **feedback** table: User feedback with metadata
- **search_index** table: Search index cache

Data will now persist across deployments! 🎉

