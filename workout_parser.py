#!/usr/bin/env python3
"""
Flexible Workout Parser
Parses workout entries to extract exercises, weights, and reps accurately
"""

import re
from typing import Dict, List, Any, Optional

def parse_exercise_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a single exercise line into structured data
    
    Formats supported:
    - "dumbbell shoulder press - 75 * 6, 5, 4" (weighted)
    - "bicep curl - 55 * 7, 60 * 4, 2; 55 * 1" (weighted with changes)
    - "pull-up - 0 * 15, 8, 8" (bodyweight with weight notation)
    - "pull-up 10, 8, 9, 7" (bodyweight, reps only)
    - "pushup 30, 25, 20, 20" (bodyweight, reps only)
    - "one leg calf raises - 75 (1 dumbbell) * 10, 10, 10"
    - "smith bench press - 225 * 5, 5, 4, 195 * 4, 105 * 10"
    """
    line = line.strip()
    if not line or line.startswith('SKIP') or line.startswith('run'):
        return None
    
    # Check for bodyweight format first: "exercise reps, reps, reps" (no dash, no asterisk)
    # Pattern: exercise name followed by comma-separated numbers
    bodyweight_pattern = r'^([a-zA-Z\s\-]+?)\s+(\d+(?:\s*,\s*\d+)*)$'
    bodyweight_match = re.match(bodyweight_pattern, line)
    
    if bodyweight_match:
        exercise_name = bodyweight_match.group(1).strip()
        reps_str = bodyweight_match.group(2)
        # Parse reps
        reps = [int(r.strip()) for r in reps_str.split(',') if r.strip().isdigit()]
        if reps:
            sets = [{'weight': 0, 'reps': r} for r in reps]
            return {
                'exercise': exercise_name,
                'sets': sets,
                'first_weight': 0,
                'first_reps': reps[0],
                'total_sets': len(sets),
                'max_weight': 0,
                'max_reps': max(reps),
                'is_bodyweight': True
            }
    
    # Pattern: exercise - weight * reps, reps, reps
    # Or: exercise - weight * reps, weight * reps (weight changes)
    
    # Split by dash to separate exercise name from weight/reps
    if ' - ' not in line:
        return None
    
    parts = line.split(' - ', 1)
    if len(parts) != 2:
        return None
    
    exercise_name = parts[0].strip()
    weight_reps_part = parts[1].strip()
    
    # Parse weight and reps
    # Handle cases like "75 (1 dumbbell) * 10" - extract just the weight
    weight_match = re.match(r'(\d+)\s*(?:\([^)]+\))?\s*\*', weight_reps_part)
    if not weight_match:
        return None
    
    first_weight = int(weight_match.group(1))
    
    # Extract all reps (after the *)
    # Format can be: "6, 5, 4" or "7, 60 * 4, 2" or "7, 60 * 4, 2; 55 * 1"
    reps_part = weight_reps_part.split('*', 1)[1] if '*' in weight_reps_part else ''
    
    # Parse reps - can be comma-separated or semicolon-separated (for weight changes)
    sets = []
    current_weight = first_weight
    
    # Split by semicolon first (major weight changes)
    if ';' in reps_part:
        # Multiple weight groups: "7, 60 * 4, 2; 55 * 1"
        # First part uses first_weight, then may have weight changes
        weight_groups = reps_part.split(';')
        for group_idx, group in enumerate(weight_groups):
            group = group.strip()
            # Parse this group - can have weight changes within it
            parts = [p.strip() for p in group.split(',')]
            for part in parts:
                if '*' in part:
                    # Weight change: "60 * 4"
                    weight_match = re.search(r'(\d+)\s*\*\s*(\d+)', part)
                    if weight_match:
                        current_weight = int(weight_match.group(1))
                        rep = int(weight_match.group(2))
                        sets.append({'weight': current_weight, 'reps': rep})
                else:
                    # Just a rep number - use current weight
                    if part.isdigit():
                        sets.append({'weight': current_weight, 'reps': int(part)})
    else:
        # No semicolon - check if weight changes within comma-separated list
        # Format: "5, 5, 4, 195 * 4, 105 * 10" or "7, 60 * 4, 2"
        parts = [p.strip() for p in reps_part.split(',')]
        for part in parts:
            if '*' in part:
                # Weight change: "195 * 4" or "60 * 4"
                weight_match = re.search(r'(\d+)\s*\*\s*(\d+)', part)
                if weight_match:
                    current_weight = int(weight_match.group(1))
                    rep = int(weight_match.group(2))
                    sets.append({'weight': current_weight, 'reps': rep})
            else:
                # Just a rep number - use current weight
                if part.isdigit():
                    sets.append({'weight': current_weight, 'reps': int(part)})
    
    if not sets:
        return None
    
    return {
        'exercise': exercise_name,
        'sets': sets,
        'first_weight': first_weight,
        'first_reps': sets[0]['reps'] if sets else None,
        'total_sets': len(sets),
        'max_weight': max(s['weight'] for s in sets),
        'max_reps': max(s['reps'] for s in sets),
        'is_bodyweight': first_weight == 0,
        'original_line': line
    }

def parse_workout_text(workout_text: str) -> Dict[str, Any]:
    """
    Parse a full workout entry into structured data
    """
    lines = [line.strip() for line in workout_text.split('\n') if line.strip()]
    
    exercises = []
    for line in lines:
        parsed = parse_exercise_line(line)
        if parsed:
            exercises.append(parsed)
    
    return {
        'exercises': exercises,
        'exercise_count': len(exercises),
        'total_sets': sum(e['total_sets'] for e in exercises)
    }

def load_exercise_mapping():
    """Load exercise name normalization mapping"""
    import json
    from pathlib import Path
    
    mapping_path = Path(__file__).parent / "exercise_mapping.json"
    if mapping_path.exists():
        try:
            return json.loads(mapping_path.read_text())
        except:
            return {}
    return {}

def normalize_exercise_name(exercise_name: str) -> tuple:
    """
    Normalize exercise name and return (normalized_name, muscle_groups)
    """
    exercise_name_lower = exercise_name.lower().strip()
    mapping = load_exercise_mapping()
    
    # Check direct match first
    if exercise_name_lower in mapping.get('mappings', {}):
        mapping_data = mapping['mappings'][exercise_name_lower]
        return mapping_data.get('normalized', exercise_name), mapping_data.get('muscle_groups', [])
    
    # Check variations
    for key, mapping_data in mapping.get('mappings', {}).items():
        variations = mapping_data.get('variations', [])
        for variation in variations:
            if variation.lower() == exercise_name_lower or variation.lower() in exercise_name_lower:
                return mapping_data.get('normalized', exercise_name), mapping_data.get('muscle_groups', [])
    
    # No mapping found, return original
    return exercise_name, []

def extract_muscle_groups_from_exercises(exercises: List[Dict], knowledge_base: Dict = None) -> List[str]:
    """
    Extract muscle groups from parsed exercises using knowledge base and exercise mapping
    """
    found_groups = set()
    
    # Load exercise mapping first
    mapping = load_exercise_mapping()
    
    # Also check knowledge base
    muscle_groups = {}
    if knowledge_base and 'muscle_groups' in knowledge_base:
        muscle_groups = knowledge_base.get('muscle_groups', {}).get('categorization', {})
    
    for exercise_data in exercises:
        exercise_name = exercise_data['exercise']
        
        # Normalize exercise name and get muscle groups from mapping
        normalized_name, mapped_groups = normalize_exercise_name(exercise_name)
        for group in mapped_groups:
            found_groups.add(group)
        
        # Also check knowledge base (handle nested structures like arms.biceps)
        exercise_name_lower = exercise_name.lower()
        for group, info in muscle_groups.items():
            if isinstance(info, dict):
                # Check primary_exercises
                if 'primary_exercises' in info:
                    for ex in info['primary_exercises']:
                        if ex.lower() in exercise_name_lower or exercise_name_lower in ex.lower():
                            found_groups.add(group)
                            break
                
                # Handle nested structures (e.g., arms.biceps, arms.triceps)
                if group == 'arms':
                    for sub_group, sub_info in info.items():
                        if isinstance(sub_info, dict) and 'primary_exercises' in sub_info:
                            for ex in sub_info['primary_exercises']:
                                if ex.lower() in exercise_name_lower or exercise_name_lower in ex.lower():
                                    found_groups.add('arms')
                                    # Add both "arms" and specific sub-group (e.g., "triceps", "biceps")
                                    # This ensures we can match on either
                                    found_groups.add(sub_group)
                                    break
    
    return list(found_groups)

def get_progression_suggestion(exercise_data: Dict, previous_workouts: List[Dict]) -> Dict[str, Any]:
    """
    Suggest progression for an exercise based on previous performance
    Returns suggested weight and reps for next workout
    """
    exercise_name = exercise_data['exercise'].lower()
    
    # Find this exercise in previous workouts
    previous_performances = []
    for workout in previous_workouts:
        parsed = parse_workout_text(workout.get('text', ''))
        for ex in parsed.get('exercises', []):
            if ex['exercise'].lower() == exercise_name:
                previous_performances.append({
                    'weight': ex['max_weight'],
                    'reps': ex['max_reps'],
                    'sets': ex['total_sets'],
                    'date': workout.get('date', '')
                })
    
    if not previous_performances:
        # No previous data - return current
        return {
            'suggested_weight': exercise_data['max_weight'],
            'suggested_reps': exercise_data['first_reps'],
            'reason': 'No previous data'
        }
    
    # Get most recent performance
    most_recent = previous_performances[0]  # Assuming workouts are sorted newest first
    
    # Simple progression: try to match or slightly increase
    suggested_weight = most_recent['weight']
    suggested_reps = most_recent['reps']
    
    # If they hit all reps easily, suggest slight increase
    # (This is simplified - could be smarter)
    
    return {
        'suggested_weight': suggested_weight,
        'suggested_reps': suggested_reps,
        'previous_weight': most_recent['weight'],
        'previous_reps': most_recent['reps'],
        'reason': 'Match previous performance'
    }

if __name__ == '__main__':
    # Test parser
    test_lines = [
        "dumbbell shoulder press - 75 * 6, 5, 4",
        "bicep curl - 55 * 7, 60 * 4, 2; 55 * 1",
        "pull-up - 0 * 15, 8, 8",
        "one leg calf raises - 75 (1 dumbbell) * 10, 10, 10",
        "smith bench press - 225 * 5, 5, 4, 195 * 4, 105 * 10"
    ]
    
    print("Testing parser:")
    for line in test_lines:
        parsed = parse_exercise_line(line)
        if parsed:
            print(f"\n{line}")
            print(f"  Exercise: {parsed['exercise']}")
            print(f"  Sets: {parsed['sets']}")
            print(f"  Max weight: {parsed['max_weight']}, Max reps: {parsed['max_reps']}")

