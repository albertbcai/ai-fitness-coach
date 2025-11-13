# Deployment Guide

## Quick Deploy Options

### Option 1: Railway (Recommended - Easiest)
1. Go to [railway.app](https://railway.app)
2. Sign up/login with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your repository
5. Railway will auto-detect Flask and deploy
6. Add environment variable: `ANTHROPIC_API_KEY=your_key_here`
7. Your app will be live at `https://your-app.railway.app`

### Option 2: Render
1. Go to [render.com](https://render.com)
2. Sign up/login
3. Click "New" → "Web Service"
4. Connect your GitHub repo
5. Settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`
6. Add environment variable: `ANTHROPIC_API_KEY`
7. Deploy!

### Option 3: Fly.io
1. Install Fly CLI: `curl -L https://fly.io/install.sh | sh`
2. Run: `fly launch`
3. Follow prompts
4. Set secret: `fly secrets set ANTHROPIC_API_KEY=your_key`
5. Deploy: `fly deploy`

### Option 4: Heroku (Legacy)
1. Install Heroku CLI
2. `heroku create your-app-name`
3. `git push heroku main`
4. `heroku config:set ANTHROPIC_API_KEY=your_key`

## Environment Variables Needed

- `ANTHROPIC_API_KEY` - Your Anthropic API key (required)

## Important Notes

1. **File Storage**: The app uses local files (workouts.md, themes.json, etc.). On most platforms, these files persist, but:
   - Railway: Files persist in the filesystem
   - Render: Files persist in the filesystem
   - Fly.io: Files persist in volumes
   - Heroku: Files are ephemeral (use database instead)

2. **Port**: The app uses `$PORT` environment variable (set automatically by platforms)

3. **HTTPS**: All platforms provide HTTPS automatically

4. **Data Backup**: Consider backing up your `workouts.md` file regularly

## Testing Locally Before Deploy

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variable
export ANTHROPIC_API_KEY=your_key_here

# Run with gunicorn (production-like)
gunicorn app:app --bind 0.0.0.0:5000

# Or run with Flask dev server
python app.py
```

## Post-Deployment

1. Visit your deployed URL
2. Test adding a workout
3. Check that feedback button works
4. Verify all features work

## Troubleshooting

- **500 errors**: Check server logs, ensure ANTHROPIC_API_KEY is set
- **File not found**: Ensure file paths work on the platform
- **Slow responses**: Check if AI calls are timing out (increase timeout in Procfile)

