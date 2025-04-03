"""
Macros -

Can put in a reading of some food
Writes to a log
"""

import json
import os
from pathlib import Path

DATA_DIR = Path("data")
FOOD_DB_FILE = DATA_DIR / "food_db.json"

# Initialise data directory & files
DATA_DIR.mkdir(exist_ok=True)

def init_food_db():
    # Initialise empty DB if it doesn't exist
    if not FOOD_DB_FILE.exists():
        FOOD_DB_FILE.write_text("{}")

def add_food(name, macros, serving_size):
    """
    Add or edit a food item in the DB
    Args:
        name (str): Food name (key)
        macros (dict): Nutritional info - {'calories': 100, 'protein': 20}
        serving_size (float): Amount of food - 100g or 200ml etc.
    """
    db = get_all_foods()
    db[name.lower()] = macros
    FOOD_DB_FILE.write_text(json.dumps(db, indent=2))

def get_food(name):
    # Gets macros for a given food
    db = get_all_foods()
    return db.get(name.lower())

def get_all_foods():
    # Returns entire DB
    try:
        return json.loads(FOOD_DB_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    
def remove_food(name):
    # Deletes a record from food DB
    db = get_all_foods()
    db.pop(name.lower(), None)
    FOOD_DB_FILE.write_text(json.dumps(db, indent=2))

