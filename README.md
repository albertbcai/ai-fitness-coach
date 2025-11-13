# AI Fitness Coach MVP

A lightweight, notes-app-style fitness coach powered by AI.

## Features

- **Freeform workout logging** - Write workouts however you want
- **AI contextual questions** - "How did you feel?" "How'd you sleep?" (freeform, not forms)
- **Progress tracking** - Detects plateaus, suggests next steps
- **Workout suggestions** - AI recommends what to do next based on your history
- **Minimal UI** - Looks like a notes app, but smarter

## Tech Stack

- Frontend: Simple HTML/CSS/JS (notes-app aesthetic)
- Backend: Python (Flask/FastAPI) with Claude API
- Storage: Markdown files (like your workout_log.md)

## Getting Started

1. **Set up environment:**
   ```bash
   cd ai-fitness-coach
   pip install -r requirements.txt
   ```

2. **Add your Anthropic API key and budget limits:**
   Create a `.env` file:
   ```
   ANTHROPIC_API_KEY=your_api_key_here
   DAILY_BUDGET=1.00
   MONTHLY_BUDGET=20.00
   ```
   
   **Cost Management:**
   - Default daily budget: $1.00 (prevents runaway costs)
   - Default monthly budget: $20.00
   - App automatically tracks usage and stops when budget is exceeded
   - Typical conversation costs ~$0.01-0.05 per message
   - You can adjust budgets in `.env` file

3. **Run the app:**
   ```bash
   python app.py
   ```

4. **Open in browser:**
   Navigate to `http://localhost:5000`

## How It Works

1. **Log workouts** - Write workouts in freeform text (however you want)
2. **AI asks questions** - Coach asks contextual questions like "How did you feel?" "How'd you sleep?"
3. **Get suggestions** - AI detects plateaus, suggests next workouts, tracks progress
4. **Chat interface** - Natural conversation with your coach

The app reads your workout history from `../Knowledge/workout_log.md` to understand your patterns and progress.

