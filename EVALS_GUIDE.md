# Evals Guide - Workout Suggestions

## Overview

Evals (evaluations) measure the quality of workout suggestions. This helps us iterate and improve the AI output.

## What We're Evaluating

### 1. Format Check (40% weight)
- ✅ Has "Suggestion:" prefix
- ✅ 5-word summary (exactly ≤5 words)
- ✅ Has workout exercises in expected format
- ✅ Overall length is reasonable (<500 chars)

### 2. Justification (30% weight)
- ✅ Has one short sentence (≤20 words) explaining why this workout
- ✅ References scientific principles (recovery, frequency, etc.)

### 3. Brevity (30% weight)
- ✅ Total suggestion is brief (<100 words ideal, <150 acceptable)

## How to Run Evals

### Option 1: Test Individual Suggestions

```bash
cd ai-fitness-coach
python3 evals.py
```

This runs evals on a sample suggestion to show how it works.

### Option 2: Test Real Suggestions (API)

Add to your `.env` file:
```
RUN_EVALS=true
```

Then when you call the `/api/suggest-workout` endpoint, it will:
- Generate the suggestion
- Run evals on it
- Include eval results in the response (for debugging)

### Option 3: Test Multiple Suggestions

```bash
python3 test_evals.py
```

This generates multiple suggestions and runs evals on each.

## Expected Format

**Good Example:**
```
Suggestion: Upper body strength day

It's been 3 days since your last upper body workout, so you're well recovered.

dumbbell shoulder press - 75 * 6, 5, 4
bicep curl - 55 * 7, 60 * 4, 2; 55 * 1
pull-up - 0 * 15, 8, 8
```

**Why this is good:**
- ✅ 5-word summary: "Upper body strength day"
- ✅ One sentence justification referencing recovery
- ✅ Brief (48 words total)
- ✅ Has exercises in correct format

## Iterating on Evals

**As you test, you'll find issues:**
- Suggestions too long? → Adjust brevity threshold
- Missing justification? → Strengthen prompt
- Wrong format? → Add more examples

**Update evals based on what you learn:**
- Add new checks
- Adjust weights
- Refine thresholds

## Current Eval Scores

- **Format:** 4/4 points (100%)
- **Justification:** 2/2 points (100%)
- **Brevity:** 3/3 points (100%)
- **Overall:** Weighted average (pass if ≥70%)

## Next Steps

1. Test suggestions in the app
2. See what fails
3. Adjust evals based on real issues
4. Iterate on prompt to fix issues
5. Repeat

---

*Evals are a feedback loop - use them to improve the suggestions over time.*

