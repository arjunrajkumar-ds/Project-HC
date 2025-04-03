"""
Calorie tracker -

- Creates log for each day

E.g. input for foods:
<number><unit>_<food_item>
- 200g chicken
- 350ml milk
- 2 cheese

Writes data to <data.txt>

TODO -
- Add argv CLI functionality
"""

import datetime
import json
import os
from pathlib import Path

DATA_DIR = Path("data")
DATA_FILE = "calorie_logs.json"

def init_data_file():
    # Initialise the file if it doesn't exist
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f:
            json.dump({}, f)

def get_current_date():
    # Returns current date in DD-MM-YYYY format
    return datetime.now().strftime("%Y-%m-%d")
# Other format - print(date.strftime("%A: %d/%B"))

def add_food_entry(food_name, serving_size, date=None):
    """
    Log a food consumption, references food database
    Args:
        food_name (str): Name of the food
        serving_size (float): Amount of food consumed
        date (str, optional): Date in YYYY-MM-DD format
    """
    if date is None:
        date = get_current_date()