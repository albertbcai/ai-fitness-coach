#!/usr/bin/env python3
"""
Evals for Workout Suggestions
Lightweight evaluation framework for testing suggestion quality
"""

import json
import re
from typing import Dict, List, Any

def eval_suggestion_format(suggestion: str) -> Dict[str, Any]:
    """
    Evaluate if suggestion follows the required format:
    - Starts with [5-word summary] (no "Suggestion:" prefix)
    - Has workout exercises in user's format
    - Overall length is reasonable
    - NO justification line (just summary + exercises)
    """
    results = {
        'passed': True,
        'issues': [],
        'score': 0,
        'max_score': 4
    }
    
    # Check 1: Has 5-word summary (first line, no prefix)
    suggestion_lines = [line.strip() for line in suggestion.split('\n') if line.strip()]
    if len(suggestion_lines) > 0:
        first_line = suggestion_lines[0]
        # Remove "Suggestion:" prefix if present (for backwards compatibility)
        summary = re.sub(r'^Suggestion:\s*', '', first_line, flags=re.IGNORECASE).strip()
        word_count = len(summary.split())
        if word_count <= 5:
            results['score'] += 1
        else:
            results['issues'].append(f"Summary is {word_count} words (should be ≤5)")
            results['passed'] = False
    else:
        results['issues'].append("Suggestion is empty")
        results['passed'] = False
    
    # Check 2: NO justification line (should go straight from summary to exercises)
    if len(suggestion_lines) > 1:
        second_line = suggestion_lines[1]
        # Check if second line looks like a justification (not an exercise)
        justification_indicators = [
            'last exercised', 'last trained', 'ready for', 'haven\'t trained',
            'days ago', 'day ago', 'progressive overload'
        ]
        is_justification = any(indicator in second_line.lower() for indicator in justification_indicators)
        if is_justification and not re.search(r'\w+\s*-\s*\d+', second_line):
            results['issues'].append("Has justification line - should be removed (just summary + exercises)")
            results['score'] -= 0.5  # Penalize but don't fail
    
    # Check 3: Has workout exercises (check for exercise-like patterns)
    # Look for patterns like "exercise - weight * reps" or similar
    has_exercises = False
    if len(suggestion_lines) > 1:
        workout_text = '\n'.join(suggestion_lines[1:])
        # Check for common exercise patterns
        exercise_patterns = [
            r'\w+\s*-\s*\d+',  # "exercise - 75"
            r'\w+\s*\*\s*\d+',  # "exercise * 10"
            r'\d+\s*\*\s*\d+',  # "75 * 10"
        ]
        for pattern in exercise_patterns:
            if re.search(pattern, workout_text):
                has_exercises = True
                break
    
    if has_exercises:
        results['score'] += 1
    else:
        results['issues'].append("No workout exercises found in expected format")
        results['passed'] = False
    
    # Check 4: Overall length is reasonable (not too long)
    total_length = len(suggestion)
    if total_length < 500:  # Reasonable upper bound
        results['score'] += 1
    else:
        results['issues'].append(f"Suggestion is too long ({total_length} chars, should be <500)")
        results['passed'] = False
    
    results['score_pct'] = (results['score'] / results['max_score']) * 100
    
    return results

def eval_suggestion_justification(suggestion: str, workout_history: str = "") -> Dict[str, Any]:
    """
    Evaluate if suggestion includes a brief scientific justification
    - Should have one short sentence explaining why this workout
    - Should reference recovery, frequency, or other scientific principles
    - Should be factually accurate (recovery times make sense)
    """
    results = {
        'passed': False,
        'has_justification': False,
        'justification_text': '',
        'references_science': False,
        'factually_accurate': True,
        'score': 0,
        'max_score': 3
    }
    
    # Look for justification sentence (usually after summary, before exercises)
    suggestion_lines = [line.strip() for line in suggestion.split('\n') if line.strip()]
    
    # Find justification (usually line 2 or 3, between summary and exercises)
    justification = None
    for i, line in enumerate(suggestion_lines):
        if i > 0 and not re.search(r'\w+\s*-\s*\d+', line):
            # This might be a justification line
            word_count = len(line.split())
            if word_count <= 15:  # Very brief (updated from 20)
                justification = line
                results['has_justification'] = True
                results['justification_text'] = line
                results['score'] += 1
                
                # Check for fluff words (bad) - penalize
                fluff_words = ['this workout focuses on', 'this workout', 'focuses on', 'workout focuses', 'workout is']
                has_fluff = any(fluff in line.lower() for fluff in fluff_words)
                if has_fluff:
                    results['issues'] = results.get('issues', [])
                    results['issues'].append("Justification contains fluff - should be more specific and actionable")
                    results['score'] -= 0.5  # Penalize fluff
                break
            elif word_count > 15:
                results['issues'] = results.get('issues', [])
                results['issues'].append(f"Justification too long: {word_count} words (should be ≤15)")
    
    # Check if justification references scientific principles
    if justification:
        science_keywords = [
            'recovery', 'recovered', 'days since', 'frequency', 'haven\'t trained',
            'progressive', 'overload', 'muscle group', 'adequate', 'ready'
        ]
        justification_lower = justification.lower()
        for keyword in science_keywords:
            if keyword in justification_lower:
                results['references_science'] = True
                results['score'] += 1
                break
        
        # Check factual accuracy - recovery times should make sense
        # Look for patterns like "X days ago" or "X day ago" or "X.X days ago"
        days_pattern = re.search(r'(\d+\.?\d*)\s+day', justification_lower)
        if days_pattern:
            days_ago_str = days_pattern.group(1)
            days_ago = float(days_ago_str)
            
            # Check for decimal days (user wants whole numbers)
            if '.' in days_ago_str:
                results['factually_accurate'] = False
                results['issues'] = results.get('issues', [])
                results['issues'].append(f"Uses decimal days ({days_ago_str}) - should use whole numbers only")
                results['score'] -= 0.5  # Penalize decimal days
            
            # Recovery time claims should be reasonable
            # If it says "1 day ago, ready for progressive overload" - that's suspicious
            # If it says "3+ days ago" - that's reasonable
            if days_ago < 2 and ('ready' in justification_lower or 'progressive' in justification_lower):
                results['factually_accurate'] = False
                results['issues'] = results.get('issues', [])
                results['issues'].append(f"Claims ready for training after only {int(days_ago)} day(s) - recovery typically needs 48-72 hours (2+ days)")
                results['score'] -= 0.5  # Penalize factual inaccuracy
            elif days_ago >= 2:
                if results.get('factually_accurate', True):  # Only add bonus if not already penalized
                    results['factually_accurate'] = True
                    results['score'] += 0.5  # Bonus for reasonable recovery time
    
    results['score_pct'] = (results['score'] / results['max_score']) * 100
    results['passed'] = results['score'] >= 1  # At least has justification
    
    return results

def eval_suggestion_brevity(suggestion: str) -> Dict[str, Any]:
    """
    Evaluate if suggestion is brief and concise
    """
    results = {
        'passed': True,
        'word_count': 0,
        'char_count': len(suggestion),
        'score': 0,
        'max_score': 3
    }
    
    word_count = len(suggestion.split())
    results['word_count'] = word_count
    
    # Score based on brevity
    if word_count < 50:
        results['score'] = 3
    elif word_count < 100:
        results['score'] = 2
    elif word_count < 150:
        results['score'] = 1
    else:
        results['issues'] = [f"Too long: {word_count} words (should be <100)"]
        results['passed'] = False
    
    results['score_pct'] = (results['score'] / results['max_score']) * 100
    
    return results

def eval_suggestion_quality(suggestion: str, workout_history: str = "") -> Dict[str, Any]:
    """
    Comprehensive evaluation of suggestion quality
    Combines all eval functions
    """
    results = {
        'format': eval_suggestion_format(suggestion),
        'justification': eval_suggestion_justification(suggestion, workout_history),
        'brevity': eval_suggestion_brevity(suggestion),
        'overall_score': 0,
        'overall_passed': False
    }
    
    # Calculate overall score (weighted)
    format_weight = 0.4
    justification_weight = 0.35  # Increased weight for justification (includes accuracy)
    brevity_weight = 0.25
    
    results['overall_score'] = (
        results['format']['score_pct'] * format_weight +
        results['justification']['score_pct'] * justification_weight +
        results['brevity']['score_pct'] * brevity_weight
    )
    
    # Pass if overall score is >= 70% AND factually accurate
    results['overall_passed'] = results['overall_score'] >= 70 and results['justification'].get('factually_accurate', True)
    
    return results

def run_evals(suggestion: str, workout_history: str = "") -> Dict[str, Any]:
    """
    Run all evals on a suggestion and return results
    """
    return eval_suggestion_quality(suggestion, workout_history)

def print_eval_results(results: Dict[str, Any]):
    """
    Pretty print eval results
    """
    print("\n" + "="*50)
    print("EVAL RESULTS")
    print("="*50)
    
    print(f"\n📋 Format Check: {results['format']['score']}/{results['format']['max_score']} ({results['format']['score_pct']:.0f}%)")
    if results['format']['issues']:
        print("   Issues:")
        for issue in results['format']['issues']:
            print(f"   - {issue}")
    
    print(f"\n💡 Justification: {results['justification']['score']}/{results['justification']['max_score']} ({results['justification']['score_pct']:.0f}%)")
    if results['justification']['has_justification']:
        print(f"   Text: \"{results['justification']['justification_text']}\"")
        if results['justification']['references_science']:
            print("   ✅ References scientific principles")
        else:
            print("   ⚠️  Does not reference scientific principles")
        if results['justification'].get('factually_accurate', True):
            print("   ✅ Factually accurate")
        else:
            print("   ❌ Factually inaccurate - recovery time claims don't make sense")
            if results['justification'].get('issues'):
                for issue in results['justification']['issues']:
                    print(f"      - {issue}")
    else:
        print("   ⚠️  No justification found")
    
    print(f"\n📏 Brevity: {results['brevity']['score']}/{results['brevity']['max_score']} ({results['brevity']['score_pct']:.0f}%)")
    print(f"   Words: {results['brevity']['word_count']}, Chars: {results['brevity']['char_count']}")
    
    print(f"\n🎯 Overall Score: {results['overall_score']:.1f}%")
    if results['overall_passed']:
        print("   ✅ PASSED")
    else:
        print("   ❌ FAILED")
    
    print("="*50 + "\n")

if __name__ == '__main__':
    # Example usage
    test_suggestion = """Suggestion: Upper body strength day

It's been 3 days since your last upper body workout, so you're well recovered.

dumbbell shoulder press - 75 * 6, 5, 4
bicep curl - 55 * 7, 60 * 4, 2; 55 * 1
pull-up - 0 * 15, 8, 8"""
    
    results = run_evals(test_suggestion)
    print_eval_results(results)

