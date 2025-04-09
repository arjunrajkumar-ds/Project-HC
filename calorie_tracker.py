"""
sys.args:
    -v / --view: see today's eaten foods
    -l / --log: add entries
"""

# TODO - consumption_log is resetting each time. Need to seperate read & write to persist

import json
import os
import sys
from pprint import pprint

from common import get_current_date

DATA_FILE = os.path.join("data", "consumption_log.json")

def load_file():
    try:
        with open(DATA_FILE, "r") as file:
            data = json.load(file)
    except FileNotFoundError:
        data = {}
    return data

def write_file(log):
    with open(DATA_FILE, "w") as file:
        json.dump(log, file, indent=4)

def read_file():
    try:
        with open(DATA_FILE, "r") as file:
            data = json.load(file)
            # data.close()
    except FileNotFoundError:
        print("Missing file")
        data = {}
    return data


def log_foods():
    consumed = []
    output = {}
    date = get_current_date()
    output["date"] = date
    output["foods"] = []

    # Takes line by line input of what user has eaten
    # Format: <measurement><unit>_<food_item>
    while True:
        try:
            foods = input("What have you eaten today?\n~ to save:\n")
            consumed.append(foods)
            if foods == '~':
                break
        except (KeyboardInterrupt, EOFError):
            break
        output["foods"] = consumed
    write_file(output)

def view_food(log_file):
    pprint(log_file)


def main():
    
    if sys.argv[1] == "l" or sys.argv[1] == "--log":
        try:
            log_foods()
        except (IndexError):
            print("Missing command-line argument: -l or -v")
            pass
    elif sys.argv[1] == "-v" or sys.argv[1] == "--view":
        try:
            logs = read_file()
            view_food(logs)
        except IndexError:
            pass

if __name__ == "__main__":
    main()