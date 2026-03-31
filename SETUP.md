# Accessing the App on Your iPhone at the Gym

This guide walks through using Tailscale to open the app on your iPhone from anywhere — at the gym, on the train, wherever — with no paid services and no complicated router configuration.

---

## What Tailscale does

Tailscale creates a private, encrypted connection between your devices. Once set up, your iPhone and your Mac behave as if they're on the same local network, even if they're in completely different locations. The app running on your Mac becomes reachable from your phone in seconds.

It's free for personal use and takes about five minutes to set up.

---

## Step 1 — Install Tailscale on your Mac

1. Go to [tailscale.com](https://tailscale.com) and click **Download**.
2. Download and open the Mac installer. It will appear in your Applications folder.
3. Open Tailscale. A small icon (it looks like a stylised network symbol) will appear in your **menu bar** at the top of your screen.
4. Click that icon and choose **Log in**. Sign in with a Google or GitHub account, or create a free Tailscale account.
5. Once you're signed in, Tailscale will show **Connected** in the menu.

---

## Step 2 — Install Tailscale on your iPhone

1. Open the **App Store** on your iPhone and search for **Tailscale**.
2. Download and open the app.
3. Sign in with the **same account** you used on your Mac. This is important — both devices need to be on the same Tailscale account to see each other.
4. Tailscale will ask permission to set up a VPN configuration on your iPhone. Tap **Allow** — this is how it creates the private connection.
5. Once signed in, the app will show your devices. You should see your Mac listed.

---

## Step 3 — Find your Mac's Tailscale address

1. On your Mac, click the **Tailscale icon** in the menu bar.
2. You'll see your Mac's name with an IP address underneath it. It will start with `100.` — for example `100.64.12.34`. This is your private Tailscale address.
3. Note this address down, or keep the menu open while you set up your phone.

---

## Step 4 — Check the app is ready to accept connections

The app is already configured to accept connections from other devices. When you run it, it binds to all network interfaces — meaning Tailscale traffic can reach it, not just your own Mac's browser.

When you start the app with `python app.py`, you should see something like:

```
* Running on http://0.0.0.0:5001
```

The `0.0.0.0` part is what makes this work — it means "listen for connections coming from anywhere", not just from the Mac itself. If you ever see `127.0.0.1` instead, the app won't be reachable from your phone.

> **Note on port 5001:** The app runs on port 5001 rather than the more common 5000. This is because macOS uses port 5000 for AirPlay Receiver, which would cause a conflict. If you want to change the port, edit the last line of `app.py`.

---

## Step 5 — Open the app on your iPhone

1. Make sure the app is running on your Mac (you should see the Terminal window with the server output).
2. On your iPhone, open **Safari**.
3. In the address bar, type:

```
http://100.x.x.x:5001
```

Replace `100.x.x.x` with your actual Tailscale address from Step 3. Don't forget the `http://` at the start — Safari won't assume it for local addresses.

4. The app should load. If it doesn't, double-check:
   - The Mac is on and not sleeping
   - `python app.py` is still running in Terminal
   - Tailscale shows **Connected** on both devices
   - You're typing the address correctly, including `:5001`

---

## Step 6 — Save it to your home screen (recommended)

Adding the app to your iPhone home screen gives it an app-like feel: full screen, no browser chrome, one-tap access.

1. With the app open in Safari, tap the **Share button** — the square icon with an arrow pointing upward, at the bottom of the screen.
2. Scroll down in the share sheet and tap **Add to Home Screen**.
3. Give it a name — something like **Gym Tracker** — and tap **Add**.

It will appear on your home screen like any other app. Tapping it opens directly to the dashboard, full screen.

---

## Keeping it accessible from anywhere

As long as:
- Your Mac is **on** (not sleeping — you may want to set your Mac's sleep settings to "never" while you're at the gym, or use a tool like Amphetamine from the App Store to keep it awake)
- `python app.py` is **running** in Terminal
- Tailscale is **connected** on both devices

…you can open the app on your iPhone from anywhere in the world. The Tailscale connection is encrypted and private — no one else can reach your app.

---

## Troubleshooting

**The page won't load on my phone**

- Check that the Terminal window on your Mac still shows the server running. If it stopped, run `python app.py` again.
- Check that Tailscale shows "Connected" on your iPhone (open the Tailscale app to check).
- Make sure you're using `http://` not `https://` — the app doesn't use HTTPS.
- Try the address in a fresh Safari tab rather than a saved bookmark, in case the address changed.

**My Mac's Tailscale address changed**

Tailscale addresses (`100.x.x.x`) are stable and don't change for your devices. If it looks different, make sure you're signed into the same account on both devices.

**The app is slow over Tailscale**

This is usually a sign that your Mac's internet connection is the bottleneck. The app is very lightweight — it should load near-instantly. If it feels slow, check your Mac's connection.

**I don't want to leave Terminal open**

You can run the app in the background by appending `&` to the command: `python app.py &`. To stop it later, find the process with `ps aux | grep app.py` and kill it by its process ID. Most people find it easier to just leave Terminal running in the background.
