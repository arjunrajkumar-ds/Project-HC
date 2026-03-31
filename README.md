# Gym & Swim Tracker

## What this is

A personal app for tracking gym workouts and swim sessions. You log what you lifted — exercise, weight, sets, reps — and what you swam. The app keeps track of your history, shows your personal records, suggests which exercise to lead with each session based on what you've done least recently, and charts your progress over time.

It lives entirely on your own Mac. There are no accounts, no subscriptions, and no data shared with anyone.

---

## How it works

A small server runs quietly on your Mac in the background. You open the app like a website — just type an address into Safari or Chrome. Your workout data is saved in a single file on your Mac, not in the cloud.

Because it runs on your local network, you can also open it on your iPhone while you're at the gym — either over your home Wi-Fi, or from anywhere using a free tool called Tailscale (more on that below).

---

## First-time setup

You only need to do this once.

**1. Install Python**

The app runs on Python 3. To check if you already have it, open the Terminal app (you can find it in Applications → Utilities) and type `python3 --version`, then press Return. If you see a version number like `Python 3.11.4`, you're good. If not, go to [python.org/downloads](https://www.python.org/downloads/) and download the latest version for Mac. Run the installer — it's just a standard Mac app install.

**2. Get the project folder**

Download or copy the project folder (the one containing `app.py`) onto your Mac. Put it somewhere easy to find, like your Desktop or Documents folder.

**3. Open Terminal in that folder**

Open the Terminal app. Then drag the project folder onto the Terminal window — this automatically navigates you into the right place. Alternatively, type `cd ` (with a space after it), then drag the folder in, then press Return.

**4. Set up the environment (one time only)**

In Terminal, run these two lines one at a time, pressing Return after each:

```
python3 -m venv venv
source venv/bin/activate
pip install flask
```

This installs Flask, the only thing the app needs to run.

---

## Starting the app

Each time you want to use the app, open Terminal, navigate to the project folder (drag it onto Terminal), then run:

```
source venv/bin/activate
python app.py
```

You'll see a message saying the server is running. Open Safari or Chrome and go to:

```
http://localhost:5001
```

The app will open. To stop it, go back to Terminal and press Control + C.

---

## Using the app on your iPhone at the gym

The short version: install **Tailscale** (free) on your Mac and iPhone, sign into the same account, and the app becomes reachable from your phone anywhere in the world.

Quick steps:

1. Download Tailscale on your Mac from [tailscale.com](https://tailscale.com) and sign in
2. Download Tailscale on your iPhone from the App Store and sign in with the same account
3. Click the Tailscale icon in your Mac's menu bar — note the `100.x.x.x` address shown under your Mac's name
4. With the app running on your Mac, open Safari on your iPhone and go to `http://100.x.x.x:5001` (use your actual address)
5. In Safari, tap Share → Add to Home Screen to save it as a one-tap icon

For full step-by-step instructions, screenshots descriptions, troubleshooting, and notes on keeping your Mac awake, see **[SETUP.md](SETUP.md)**.

---

## Features

- **Log a gym session** — choose the day type (Push, Pull, Legs, or Core), log each exercise with sets, reps, and weight in kg
- **Tier system** — exercises are organised into three tiers: heavy compounds (Tier 1), supporting compounds (Tier 2), and aesthetics (Tier 3). Tier 2 exercises are filtered to the relevant day type automatically
- **Tier 1 rotation** — the app tracks which heavy compound you've done least recently and suggests it at the start of each session. You can override it if you want
- **Core days** — simplified: just pick your Tier 1 lift and any Tier 3 extras. No Tier 2 clutter
- **Log a swim** — pick the number of sets and whether you're doing 50m or 100m per set. The total distance is calculated for you
- **Session history** — every session is saved and listed in date order. Tap any session to see the full breakdown
- **Personal records** — the app tracks your heaviest set ever for each exercise
- **Analytics** — charts showing your lift progression over time, weekly volume lifted, and weekly swim distance
- **Works on iPhone** — the whole app is designed for phone use: large buttons, easy inputs, no fiddly small text
- **Exercise library** — view all exercises by tier, add your own, or delete ones you don't use

---

## Your data

Everything is stored in a single file called `tracker.db` in the project folder on your Mac. That's it — one file, on your machine, nowhere else.

**To back it up:** just copy `tracker.db` to another location — an external drive, iCloud, anywhere you like. Copy it back to restore.

**Nothing is sent to the internet.** The app never makes any network requests. It doesn't phone home, it doesn't sync, it doesn't collect anything. Your training data is yours.
