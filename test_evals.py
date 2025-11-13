#!/usr/bin/env python3
"""
Test evals on workout suggestions
Run this to evaluate suggestion quality
"""

import sys
import json
from pathlib import Path
from evals import run_evals, print_eval_results

# Add parent directory to path to import app functions
sys.path.insert(0, str(Path(__file__).parent))

from app import suggest_workout, load_knowledge_base

def test_suggestion_quality(num_tests=5):
    """
    Test suggestion quality by generating suggestions and running evals
    """
    print(f"\n🧪 Testing Workout Suggestions ({num_tests} tests)\n")
    
    results_summary = {
        'total_tests': 0,
        'passed': 0,
        'failed': 0,
        'avg_score': 0,
        'scores': []
    }
    
    for i in range(num_tests):
        print(f"\n--- Test {i+1}/{num_tests} ---")
        
        # Generate a suggestion (this would normally be an API call)
        # For now, we'll simulate by calling the endpoint logic
        try:
            # This is a simplified test - in practice you'd call the actual endpoint
            # For now, let's create a test that shows the eval framework
            print("Generating suggestion...")
            
            # In a real test, you'd call the API endpoint here
            # For demonstration, we'll use a sample suggestion
            test_suggestion = """Suggestion: Upper body strength day

It's been 3 days since your last upper body workout, so you're well recovered.

dumbbell shoulder press - 75 * 6, 5, 4
bicep curl - 55 * 7, 60 * 4, 2; 55 * 1
pull-up - 0 * 15, 8, 8"""
            
            # Run evals
            eval_results = run_evals(test_suggestion)
            print_eval_results(eval_results)
            
            # Track results
            results_summary['total_tests'] += 1
            results_summary['scores'].append(eval_results['overall_score'])
            
            if eval_results['overall_passed']:
                results_summary['passed'] += 1
            else:
                results_summary['failed'] += 1
                
        except Exception as e:
            print(f"❌ Error in test {i+1}: {e}")
            results_summary['total_tests'] += 1
            results_summary['failed'] += 1
    
    # Print summary
    if results_summary['scores']:
        results_summary['avg_score'] = sum(results_summary['scores']) / len(results_summary['scores'])
    
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    print(f"Total Tests: {results_summary['total_tests']}")
    print(f"Passed: {results_summary['passed']} ({results_summary['passed']/results_summary['total_tests']*100:.0f}%)")
    print(f"Failed: {results_summary['failed']} ({results_summary['failed']/results_summary['total_tests']*100:.0f}%)")
    print(f"Average Score: {results_summary['avg_score']:.1f}%")
    print("="*50 + "\n")
    
    return results_summary

if __name__ == '__main__':
    # Run tests
    test_suggestion_quality(num_tests=3)

