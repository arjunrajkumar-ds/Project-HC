#!/usr/bin/env python3
"""Thin entrypoint. Run:  python3 app.py

The Flask application and all routes live in gymtracker/app.py.
This launcher exists so the documented `python3 app.py` command keeps working.
"""
from gymtracker.app import app, init_db

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)
