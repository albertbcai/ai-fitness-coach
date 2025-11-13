// Workout Notes App - Simple Notes Interface

let workouts = [];

// DOM Elements
const newWorkoutInput = document.getElementById('new-workout-input');
const addWorkoutBtn = document.getElementById('add-workout-btn');
const workoutEntries = document.getElementById('workout-entries');
const suggestionsContainer = document.getElementById('progressive-overload-suggestions');
const suggestionsContent = document.getElementById('suggestions-content');
const closeSuggestionsBtn = document.getElementById('close-suggestions-btn');
const postWorkoutInsight = document.getElementById('post-workout-insight');
const insightText = document.getElementById('insight-text');
const closeInsightBtn = document.getElementById('close-insight-btn');
const recoveryCheck = document.getElementById('recovery-check');
const recoveryText = document.getElementById('recovery-text');
const closeRecoveryBtn = document.getElementById('close-recovery-btn');
const addNeglectedWorkoutBtn = document.getElementById('add-neglected-workout-btn');
const analyticsSection = document.getElementById('analytics-section');
const analyticsContent = document.getElementById('analytics-content');
const analyticsToggleBtn = document.getElementById('analytics-toggle-btn');
const closeAnalyticsBtn = document.getElementById('close-analytics-btn');
const workoutSearchInput = document.getElementById('workout-search');
const searchClearBtn = document.getElementById('search-clear-btn');
const feedbackBtn = document.getElementById('feedback-btn');
const feedbackModal = document.getElementById('feedback-modal');
const feedbackModalClose = document.getElementById('feedback-modal-close');
const feedbackCancelBtn = document.getElementById('feedback-cancel-btn');
const feedbackSubmitBtn = document.getElementById('feedback-submit-btn');
const feedbackText = document.getElementById('feedback-text');

// Load workouts on page load
loadWorkouts();
updateCurrentDate();
loadRecoveryCheck();

// Analytics toggle
analyticsToggleBtn.addEventListener('click', () => {
    if (analyticsSection.style.display === 'none') {
        loadAnalytics();
        analyticsSection.style.display = 'block';
    } else {
        analyticsSection.style.display = 'none';
    }
});

closeAnalyticsBtn.addEventListener('click', () => {
    analyticsSection.style.display = 'none';
});

// Close recovery button
closeRecoveryBtn.addEventListener('click', () => {
    recoveryCheck.style.display = 'none';
});

// Add neglected workout button
addNeglectedWorkoutBtn.addEventListener('click', async () => {
    try {
        const response = await fetch('/api/generate-neglected-workout');
        const data = await response.json();
        
        if (data.success && data.workout) {
            // Populate the input field with the workout
            newWorkoutInput.value = data.workout;
            newWorkoutInput.style.color = '#e6edf3';
            // Scroll to top to show the input
            window.scrollTo({ top: 0, behavior: 'smooth' });
            
            // Get progressive overload suggestions (same as copy feature)
            await loadProgressiveOverloadSuggestions(data.workout);
        } else {
            alert('Could not generate workout: ' + (data.message || 'Unknown error'));
        }
    } catch (error) {
        console.error('Error generating neglected workout:', error);
        alert('Error generating workout. Please try again.');
    }
});

// Load recovery check
async function loadRecoveryCheck() {
    try {
        const response = await fetch('/api/recovery-check');
        const data = await response.json();
        
        if (data.success && data.recovery_status) {
            // Use formatted version if available, otherwise plain text
            if (data.recovery_status_formatted) {
                recoveryText.innerHTML = data.recovery_status_formatted;
            } else {
                recoveryText.textContent = data.recovery_status;
            }
            
            // Show "Add" button if there are neglected groups OR ready-to-train groups
            const hasNeglected = data.neglected_groups && data.neglected_groups.length > 0;
            const hasReady = data.ready && data.ready.length > 0;
            if (hasNeglected || hasReady) {
                addNeglectedWorkoutBtn.style.display = 'inline-block';
            } else {
                addNeglectedWorkoutBtn.style.display = 'none';
            }
            
            recoveryCheck.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading recovery check:', error);
        // Silently fail - recovery check is nice to have
    }
}

// Load analytics
async function loadAnalytics() {
    try {
        const response = await fetch('/api/analytics');
        const data = await response.json();
        
        if (data.success && data.analytics) {
            const analytics = data.analytics;
            let html = '';
            
            // Strength Trends
            html += '<div class="analytics-card">';
            html += '<div class="analytics-card-title">Strength Trends</div>';
            html += `<div class="analytics-card-insight">${analytics.strength_trends.insight}</div>`;
            if (analytics.strength_trends.exercises && analytics.strength_trends.exercises.length > 0) {
                html += '<ul class="analytics-card-list">';
                analytics.strength_trends.exercises.forEach(ex => {
                    html += `<li>${ex.exercise}: ${ex.improvement} (${ex.percent})</li>`;
                });
                html += '</ul>';
            }
            html += '</div>';
            
            // Consistency
            html += '<div class="analytics-card">';
            html += '<div class="analytics-card-title">Consistency</div>';
            html += `<div class="analytics-card-insight">${analytics.consistency.insight}</div>`;
            html += `<div class="analytics-card-data">${analytics.consistency.workouts_per_week} workouts/week • ${analytics.consistency.current_streak}-day streak</div>`;
            html += '</div>';
            
            // Plateaus
            html += '<div class="analytics-card">';
            html += '<div class="analytics-card-title">Plateau Detection</div>';
            html += `<div class="analytics-card-insight">${analytics.plateaus.insight}</div>`;
            if (analytics.plateaus.exercises && analytics.plateaus.exercises.length > 0) {
                html += '<ul class="analytics-card-list">';
                analytics.plateaus.exercises.forEach(plateau => {
                    html += `<li>${plateau.exercise}: ${plateau.current} (${plateau.weeks_stagnant} weeks stagnant)</li>`;
                });
                html += '</ul>';
            }
            html += '</div>';
            
            // Muscle Balance
            html += '<div class="analytics-card">';
            html += '<div class="analytics-card-title">Muscle Group Balance</div>';
            html += `<div class="analytics-card-insight">${analytics.muscle_balance.insight}</div>`;
            if (analytics.muscle_balance.imbalances && analytics.muscle_balance.imbalances.length > 0) {
                html += '<ul class="analytics-card-list">';
                analytics.muscle_balance.imbalances.forEach(imbalance => {
                    html += `<li>${imbalance.group}: ${imbalance.count}x (vs ${imbalance.vs_max}x most trained)</li>`;
                });
                html += '</ul>';
            }
            html += '</div>';
            
            analyticsContent.innerHTML = html;
        }
    } catch (error) {
        console.error('Error loading analytics:', error);
        analyticsContent.innerHTML = '<div class="analytics-card-insight">Error loading analytics. Please try again.</div>';
    }
}

// Close suggestions button
closeSuggestionsBtn.addEventListener('click', () => {
    suggestionsContainer.style.display = 'none';
});

// Close insight button
closeInsightBtn.addEventListener('click', () => {
    postWorkoutInsight.style.display = 'none';
});

// Load post-workout insight
async function loadPostWorkoutInsight(workoutText) {
    try {
        console.log('Loading post-workout insight for:', workoutText.substring(0, 50));
        const response = await fetch('/api/post-workout-insight', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workout: workoutText })
        });
        
        const data = await response.json();
        console.log('Insight response:', data);
        
        if (data.success && data.insight) {
            insightText.textContent = data.insight;
            postWorkoutInsight.style.display = 'block';
            console.log('Insight displayed:', data.insight);
            
            // Check if it's a PR or strength increase - trigger confetti!
            const insightLower = data.insight.toLowerCase();
            if (insightLower.includes('pr reached') || insightLower.includes('new prs') || insightLower.includes('big accomplishment') || insightLower.includes('strength increase')) {
                triggerConfetti();
            }
            
            // No auto-hide - user can close it manually
        } else {
            console.log('No insight to show:', data);
        }
    } catch (error) {
        console.error('Error loading insight:', error);
        // Silently fail - insight is nice to have, not critical
    }
}

// Load progressive overload suggestions
async function loadProgressiveOverloadSuggestions(workoutText) {
    try {
        console.log('Loading suggestions for workout:', workoutText.substring(0, 100));
        const response = await fetch('/api/progressive-overload-suggestions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workout: workoutText })
        });
        
        const data = await response.json();
        console.log('Suggestions response:', data);
        
        if (data.success && data.suggestions && data.suggestions.length > 0) {
            console.log('Found', data.suggestions.length, 'suggestions');
            // Display suggestions
            suggestionsContent.innerHTML = '';
            data.suggestions.forEach(suggestion => {
                const suggestionDiv = document.createElement('div');
                suggestionDiv.className = 'suggestion-item';
                suggestionDiv.innerHTML = `
                    <div class="suggestion-exercise">${suggestion.exercise}</div>
                    <div class="suggestion-details">
                        <span class="suggestion-current">Last: ${suggestion.last_performance || suggestion.current}</span>
                        <span class="suggestion-arrow">→</span>
                        <span class="suggestion-suggested">${suggestion.suggested}</span>
                    </div>
                    <div class="suggestion-reason">${suggestion.reason} (${suggestion.last_done})</div>
                `;
                suggestionsContent.appendChild(suggestionDiv);
            });
            suggestionsContainer.style.display = 'block';
        } else {
            console.log('No suggestions to show:', data);
            // No suggestions or all match current
            suggestionsContainer.style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading suggestions:', error);
        suggestionsContainer.style.display = 'none';
    }
}

// Confetti celebration function
function triggerConfetti() {
    const confettiCount = 120; // More confetti for bigger celebration
    const colors = ['#58a6ff', '#f85149', '#3fb950', '#d29922', '#a5a5ff', '#ff6b9d', '#ffd93d'];
    
    // Stagger the confetti slightly for a more natural effect
    for (let i = 0; i < confettiCount; i++) {
        setTimeout(() => {
            createConfettiPiece(colors[Math.floor(Math.random() * colors.length)]);
        }, i * 10); // Spread out over 1.2 seconds
    }
}

function createConfettiPiece(color) {
    const confetti = document.createElement('div');
    confetti.style.position = 'fixed';
    confetti.style.width = '8px';
    confetti.style.height = '8px';
    confetti.style.backgroundColor = color;
    confetti.style.left = Math.random() * 100 + '%';
    confetti.style.top = '-10px';
    confetti.style.borderRadius = Math.random() > 0.5 ? '50%' : '0';
    confetti.style.pointerEvents = 'none';
    confetti.style.zIndex = '9999';
    confetti.style.opacity = '0.95';
    
    document.body.appendChild(confetti);
    
    const angle = (Math.random() - 0.5) * 60; // -30 to 30 degrees
    const velocity = 20 + Math.random() * 40; // Slower fall
    const rotation = Math.random() * 360;
    const rotationSpeed = (Math.random() - 0.5) * 8;
    
    let x = parseFloat(confetti.style.left) / 100 * window.innerWidth;
    let y = -10;
    let rotationCurrent = rotation;
    const startTime = Date.now();
    const duration = 4000; // 4 seconds total
    
    const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = elapsed / duration;
        
        // Slower fall with gravity effect
        y += velocity * 0.08;
        x += Math.sin(angle * Math.PI / 180) * velocity * 0.08;
        rotationCurrent += rotationSpeed;
        
        confetti.style.left = x + 'px';
        confetti.style.top = y + 'px';
        confetti.style.transform = `rotate(${rotationCurrent}deg)`;
        
        // Much slower fade - only starts fading in the last 30% of the animation
        if (progress > 0.7) {
            confetti.style.opacity = Math.max(0, 0.95 * (1 - (progress - 0.7) / 0.3));
        }
        
        if (progress < 1 && y < window.innerHeight + 100) {
            requestAnimationFrame(animate);
        } else {
            confetti.remove();
        }
    };
    
    requestAnimationFrame(animate);
}

// Add workout
addWorkoutBtn.addEventListener('click', addWorkout);
newWorkoutInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        addWorkout();
    }
});

async function loadWorkouts() {
    try {
        const response = await fetch('/api/workouts');
        const data = await response.json();
        
        if (data.success) {
            workouts = data.workouts;
            filteredWorkouts = []; // Reset filtered workouts
            renderWorkouts();
        }
    } catch (error) {
        console.error('Error loading workouts:', error);
    }
}

let filteredWorkouts = []; // Store filtered workouts for search
let searchTimeout = null;

// Search workouts with semantic AI
async function searchWorkouts(query) {
    if (!query || query.trim().length === 0) {
        filteredWorkouts = []; // Clear filter to show all workouts
        renderWorkouts();
        return;
    }
    
    try {
        const response = await fetch('/api/search-workouts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ query: query.trim() })
        });
        
        const data = await response.json();
        
        if (data.success && data.workout_indices) {
            // Filter workouts based on AI search results
            filteredWorkouts = data.workout_indices
                .map(idx => workouts[idx])
                .filter(workout => workout !== undefined);
        } else {
            // Fallback to empty if search fails
            filteredWorkouts = [];
        }
        
        renderWorkouts();
    } catch (error) {
        console.error('Error searching workouts:', error);
        // Fallback to keyword search
        filteredWorkouts = workouts.filter(workout => 
            workout.text.toLowerCase().includes(query.toLowerCase()) ||
            (workout.theme && workout.theme.toLowerCase().includes(query.toLowerCase()))
        );
        renderWorkouts();
    }
}

// Show/hide clear button based on input
function updateClearButton() {
    if (workoutSearchInput.value.trim().length > 0) {
        searchClearBtn.style.display = 'flex';
    } else {
        searchClearBtn.style.display = 'none';
    }
}

// Clear search button handler
searchClearBtn.addEventListener('click', (e) => {
    e.preventDefault();
    workoutSearchInput.value = '';
    searchClearBtn.style.display = 'none';
    filteredWorkouts = [];
    renderWorkouts();
});

// Debounced search input handler
workoutSearchInput.addEventListener('input', (e) => {
    const query = e.target.value;
    
    // Update clear button visibility
    updateClearButton();
    
    // Clear previous timeout
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }
    
    // Debounce: wait 300ms after user stops typing
    searchTimeout = setTimeout(() => {
        searchWorkouts(query);
    }, 300);
});

// Suggested search buttons - use server-side cached index for instant results
document.querySelectorAll('.suggested-search-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        const query = btn.getAttribute('data-query');
        workoutSearchInput.value = query;
        updateClearButton(); // Show clear button
        // Server uses cached index for preset queries - instant results
        searchWorkouts(query);
    });
});

function renderWorkouts() {
    workoutEntries.innerHTML = '';
    
    // If there's an active search, use filtered workouts; otherwise show all
    const hasActiveSearch = workoutSearchInput && workoutSearchInput.value.trim().length > 0;
    const workoutsToRender = (hasActiveSearch && filteredWorkouts.length > 0) ? filteredWorkouts : (hasActiveSearch ? [] : workouts);
    
    workoutsToRender.forEach(workout => {
        const entry = createWorkoutEntry(workout);
        workoutEntries.appendChild(entry);
    });
    
    // Show message if no results from search
    if (hasActiveSearch && workoutsToRender.length === 0) {
        const noResults = document.createElement('div');
        noResults.className = 'no-results';
        noResults.textContent = 'No workouts found';
        noResults.style.textAlign = 'center';
        noResults.style.color = '#8b949e';
        noResults.style.padding = '40px 20px';
        noResults.style.fontSize = '14px';
        workoutEntries.appendChild(noResults);
    }
}

function createWorkoutEntry(workout) {
    const entryDiv = document.createElement('div');
    entryDiv.className = 'workout-entry';
    
    // Header row with date, copy button, and delete button
    const headerDiv = document.createElement('div');
    headerDiv.className = 'workout-header';
    
    const dateDiv = document.createElement('div');
    dateDiv.className = 'workout-date';
    
    // Add emojis for PRs and strength increases
    let dateText = workout.date || 'No date';
    if (workout.has_pr) {
        dateText = '🏆 ' + dateText;
    } else if (workout.has_strength_increase) {
        dateText = '📈 ' + dateText;
    }
    
    dateDiv.textContent = dateText;
    
    // Copy button (shows on hover)
    const copyBtn = document.createElement('button');
    copyBtn.className = 'workout-copy-btn';
    copyBtn.textContent = 'Copy';
    copyBtn.title = 'Copy to new workout';
    copyBtn.style.display = 'none'; // Hidden by default
    
    copyBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        // Copy workout text to input field
        newWorkoutInput.value = workout.text;
        newWorkoutInput.style.color = '#e6edf3';
        // Scroll to top to show the input
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
        // Get progressive overload suggestions
        await loadProgressiveOverloadSuggestions(workout.text);
    });
    
    // Delete button (minimalist, shows on hover)
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'workout-delete-btn';
    deleteBtn.textContent = '×';
    deleteBtn.title = 'Delete workout';
    deleteBtn.style.display = 'none'; // Hidden by default
    
    // Show buttons on hover
    entryDiv.addEventListener('mouseenter', () => {
        copyBtn.style.display = 'inline-block';
        deleteBtn.style.display = 'inline-block';
    });
    entryDiv.addEventListener('mouseleave', () => {
        copyBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
    });
    
    deleteBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm('Delete this workout?')) {
            deleteWorkout(workout.date, workout.text, entryDiv);
        }
    });
    
    headerDiv.appendChild(dateDiv);
    headerDiv.appendChild(copyBtn);
    headerDiv.appendChild(deleteBtn);
    
    // Theme display with edit button
    const themeContainer = document.createElement('div');
    themeContainer.className = 'theme-container';
    
    const themeDiv = document.createElement('div');
    themeDiv.className = 'workout-theme';
    
    // If theme exists, show it; otherwise generate it
    if (workout.theme && workout.theme !== 'null' && workout.theme !== null) {
        themeDiv.textContent = workout.theme;
    } else {
        themeDiv.textContent = '...';
        generateTheme(workout.date, workout.text, themeDiv);
    }
    
    // Edit button (small, subtle)
    const editBtn = document.createElement('button');
    editBtn.className = 'theme-edit-btn';
    editBtn.textContent = '✎';
    editBtn.title = 'Edit theme';
    editBtn.style.display = 'none'; // Hidden by default
    
    // Show edit button on hover
    themeContainer.addEventListener('mouseenter', () => {
        editBtn.style.display = 'inline-block';
    });
    themeContainer.addEventListener('mouseleave', () => {
        editBtn.style.display = 'none';
    });
    
    // Make theme editable on click
    themeDiv.addEventListener('click', () => {
        makeThemeEditable(themeDiv, workout.date, workout.text);
    });
    
    editBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        makeThemeEditable(themeDiv, workout.date, workout.text);
    });
    
    themeContainer.appendChild(themeDiv);
    themeContainer.appendChild(editBtn);
    
    const textDiv = document.createElement('div');
    textDiv.className = 'workout-text';
    textDiv.textContent = workout.text;
    textDiv.contentEditable = true;
    
    // Save on blur (when clicking away or tabbing out)
    textDiv.addEventListener('blur', () => {
        const newText = textDiv.textContent.trim();
        if (newText !== workout.text) {
            updateWorkout(workout.date, workout.text, newText);
            workout.text = newText;
            // Clear theme if workout changed (will regenerate)
            if (workout.theme) {
                workout.theme = null;
                themeDiv.textContent = '...';
                generateTheme(workout.date, newText, themeDiv);
            }
        }
    });
    
    // Allow Enter to create new lines normally
    // Save with Cmd+Enter (Mac) or Ctrl+Enter (Windows/Linux)
    textDiv.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            textDiv.blur(); // Save and exit edit mode
        }
        // Otherwise, Enter works normally to create new lines
    });
    
    // Append in order: header (date + copy + delete), theme, text
    entryDiv.appendChild(headerDiv);
    entryDiv.appendChild(themeContainer);
    entryDiv.appendChild(textDiv);
    
    return entryDiv;
}

function makeThemeEditable(themeElement, workoutDate, workoutText) {
    const currentTheme = themeElement.textContent.trim();
    themeElement.contentEditable = true;
    themeElement.focus();
    
    // Select all text
    const range = document.createRange();
    range.selectNodeContents(themeElement);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    
    // Save on blur
    const saveTheme = () => {
        const newTheme = themeElement.textContent.trim();
        if (newTheme && newTheme !== currentTheme) {
            updateTheme(workoutDate, workoutText, newTheme);
        } else if (!newTheme) {
            themeElement.textContent = currentTheme;
        }
        themeElement.contentEditable = false;
    };
    
    themeElement.addEventListener('blur', saveTheme, { once: true });
    themeElement.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            themeElement.blur();
        }
    });
}

async function generateTheme(workoutDate, workoutText, themeElement) {
    if (!workoutText || !workoutText.trim()) {
        themeElement.textContent = '';
        return;
    }
    
    try {
        const response = await fetch('/api/generate-theme', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ 
                workout_date: workoutDate,
                workout_text: workoutText 
            })
        });
        
        const data = await response.json();
        
        if (data.success && data.theme) {
            themeElement.textContent = data.theme;
        } else {
            console.error('Theme generation failed:', data.error || 'Unknown error');
            themeElement.textContent = '';
        }
    } catch (error) {
        console.error('Error generating theme:', error);
        themeElement.textContent = '';
    }
}

async function updateTheme(workoutDate, workoutText, newTheme) {
    try {
        const response = await fetch('/api/update-theme', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                workout_date: workoutDate,
                workout_text: workoutText,
                theme: newTheme
            })
        });
        
        const data = await response.json();
        if (data.success) {
            // Update local workout object
            const workout = workouts.find(w => w.date === workoutDate && w.text === workoutText);
            if (workout) {
                workout.theme = newTheme;
            }
        }
    } catch (error) {
        console.error('Error updating theme:', error);
    }
}

async function updateWorkout(oldDate, oldText, newText) {
    try {
        const response = await fetch('/api/update-workout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                date: oldDate,
                old_text: oldText,
                new_text: newText
            })
        });
        
        const data = await response.json();
        if (data.success) {
            // Reload workouts to get updated list
            loadWorkouts();
        }
    } catch (error) {
        console.error('Error updating workout:', error);
    }
}

async function deleteWorkout(workoutDate, workoutText, entryElement) {
    try {
        const response = await fetch('/api/delete-workout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                workout_date: workoutDate,
                workout_text: workoutText
            })
        });
        
        const data = await response.json();
        if (data.success) {
            // Remove from DOM
            entryElement.remove();
            // Remove from local array
            workouts = workouts.filter(w => !(w.date === workoutDate && w.text === workoutText));
            // Reload from server to ensure we're in sync (in case workout was in multiple files)
            setTimeout(async () => {
                await loadWorkouts();
            }, 100);
        } else {
            alert('Error: ' + (data.error || 'Failed to delete workout'));
        }
    } catch (error) {
        console.error('Error deleting workout:', error);
        alert('Error deleting workout: ' + error.message);
    }
}

async function addWorkout() {
    const workoutText = newWorkoutInput.value.trim();
    if (!workoutText) {
        return;
    }
    
    try {
        const response = await fetch('/api/log-workout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ workout: workoutText })
        });
        
        const data = await response.json();
        if (data.success && data.entry) {
            // Clear input
            newWorkoutInput.value = '';
            
            // Hide progressive overload suggestions
            suggestionsContainer.style.display = 'none';
            
            // Small delay to ensure file is written, then reload
            setTimeout(async () => {
                await loadWorkouts();
                // Scroll to top to see the new workout
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }, 100);
            
            // Update date
            updateCurrentDate();
            
            // Get post-workout insight
            await loadPostWorkoutInsight(workoutText);
        } else {
            alert('Error: ' + (data.error || 'Failed to add workout'));
        }
    } catch (error) {
        console.error('Error adding workout:', error);
        alert('Error adding workout: ' + error.message);
    }
}

// Feedback functionality
feedbackBtn.addEventListener('click', () => {
    feedbackModal.style.display = 'flex';
    feedbackText.value = '';
    feedbackText.focus();
});

feedbackModalClose.addEventListener('click', () => {
    feedbackModal.style.display = 'none';
});

feedbackCancelBtn.addEventListener('click', () => {
    feedbackModal.style.display = 'none';
});

feedbackSubmitBtn.addEventListener('click', async () => {
    const text = feedbackText.value.trim();
    if (!text) {
        alert('Please enter some feedback');
        return;
    }
    
    // Collect metadata about current app state
    const metadata = {
        // App state
        workoutCount: workouts.length,
        hasRecoveryCheck: document.getElementById('recovery-check').style.display !== 'none',
        analyticsOpen: document.getElementById('analytics-section').style.display !== 'none',
        searchActive: workoutSearchInput.value.trim().length > 0,
        searchQuery: workoutSearchInput.value.trim() || null,
        
        // Device/Technical
        screenWidth: window.innerWidth,
        screenHeight: window.innerHeight,
        deviceType: window.innerWidth < 768 ? 'mobile' : 'desktop',
        userAgent: navigator.userAgent,
        
        // Page context
        url: window.location.href,
        timestamp: new Date().toISOString(),
        
        // Optional: last workout date if available
        lastWorkoutDate: workouts.length > 0 ? workouts[0].date : null
    };
    
    try {
        const response = await fetch('/api/feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                feedback: text,
                metadata: metadata
            })
        });
        
        const data = await response.json();
        if (data.success) {
            feedbackModal.style.display = 'none';
            feedbackText.value = '';
            // Optional: show a brief success message
            alert('Thank you for your feedback!');
        } else {
            alert('Error submitting feedback: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Error submitting feedback:', error);
        alert('Error submitting feedback. Please try again.');
    }
});

// Close modal on outside click
feedbackModal.addEventListener('click', (e) => {
    if (e.target === feedbackModal) {
        feedbackModal.style.display = 'none';
    }
});

function updateCurrentDate() {
    const dateElement = document.getElementById('current-date');
    if (dateElement) {
        const today = new Date();
        const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
        dateElement.textContent = today.toLocaleDateString('en-US', options);
    }
}
