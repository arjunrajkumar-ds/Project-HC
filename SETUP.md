# Accessing the App on Your iPhone

This guide walks through opening the app on your iPhone over your home Wi-Fi. No paid services and no router configuration.

Both the Mac (running the app) and the iPhone must be on the **same Wi-Fi network**.

---

## Step 1 — Check the app is ready to accept connections

The app is already configured to accept connections from other devices on your network. When you run it, it binds to all network interfaces — meaning your phone can reach it, not just your own Mac's browser.

When you start the app with `python3 app.py`, you should see something like:

```
* Running on http://0.0.0.0:5001
```

The `0.0.0.0` part is what makes this work — it means "listen for connections from anywhere on the network". If you ever see `127.0.0.1` instead, the app won't be reachable from your phone.

> **Note on port 5001:** The app runs on port 5001 rather than the more common 5000. macOS uses port 5000 for AirPlay Receiver, which would conflict. To change it, edit the last line of `app.py`.

---

## Step 2 — Find your Mac's local IP address

In Terminal on your Mac, run:

```
ipconfig getifaddr en0
```

You'll see something like `192.168.1.42`. That's your Mac's address on the local network. If `en0` returns nothing, try `en1` — that's Wi-Fi on some Macs.

---

## Step 3 — Open the app on your iPhone

1. Make sure the app is running on your Mac (Terminal shows the server output).
2. On your iPhone, open **Safari**.
3. In the address bar, type:

```
http://192.168.1.42:5001
```

Replace `192.168.1.42` with your actual address from Step 2. Don't forget the `http://` at the start — Safari won't assume it for local addresses.

4. The app should load. If it doesn't, double-check:
   - Both devices are on the **same Wi-Fi network**
   - The Mac is on and not sleeping
   - `python3 app.py` is still running in Terminal
   - You're typing the address correctly, including `:5001`
   - macOS firewall isn't blocking Python (System Settings → Network → Firewall)

---

## Step 4 — Save it to your home screen (recommended)

Adding the app to your iPhone home screen gives it an app-like feel: full screen, no browser chrome, one-tap access.

1. With the app open in Safari, tap the **Share button** — the square icon with an arrow pointing upward, at the bottom of the screen.
2. Scroll down in the share sheet and tap **Add to Home Screen**.
3. Give it a name — something like **Gym Tracker** — and tap **Add**.

It will appear on your home screen like any other app. Tapping it opens directly to the dashboard, full screen.

---

## Troubleshooting

**The page won't load on my phone**

- Check both devices are on the same Wi-Fi network.
- Check the Terminal window on your Mac still shows the server running. If it stopped, run `python3 app.py` again.
- Make sure you're using `http://` not `https://` — the app doesn't use HTTPS.
- Check macOS firewall isn't blocking incoming connections to Python.

**My Mac's IP address changed**

Local IPs from home routers can shift when the Mac reconnects. If the old address stops working, re-run `ipconfig getifaddr en0` to get the current one. To make it stable, reserve a static IP for your Mac in your router settings.

**I don't want to leave Terminal open**

You can run the app in the background by appending `&` to the command: `python3 app.py &`. To stop it later, find the process with `ps aux | grep app.py` and kill it by its process ID. Most people find it easier to just leave Terminal running in the background.
