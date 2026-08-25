# Hector for Android

Your Hector nutrition/diet tracker, packaged as an Android app. The full Flask
app runs **inside** the APK (via Chaquopy, an embedded Python), shown in a
full-screen WebView — so every screen is identical to the desktop version:
dashboard, ingredients, meals, diary, hydration, steps, analytics, notes,
settings. It runs fully offline; nothing needs to be running on your PC.

**Differences from desktop:**
- **Garmin is removed.** Steps are filled automatically from the phone's own
  hardware step counter (`source='android'`). You can still edit steps by hand;
  manual edits are never overwritten.
- Your existing data (a snapshot of `hector.db` + ingredient photos) is bundled
  and copied into the app's private storage on first launch. From then on the
  **phone's copy is independent** of the PC copy — they do not sync.

---

## How the pieces fit

| Part | File(s) |
|------|---------|
| Embedded Python launcher | `app/src/main/python/mobile_main.py` |
| Steps → DB bridge | `app/src/main/python/mobile_steps.py` |
| Garmin removed (stub) | `app/src/main/python/garmin.py` |
| Your app (path-tweaked copies) | `app/src/main/python/app.py`, `database.py` |
| Seed data (bundled) | `app/src/main/assets/seed/` (hector.db, templates/, static/) |
| Android shell + step counter | `app/src/main/java/com/jakub/hector/*.kt` |

Only two lines were changed in `app.py`/`database.py`: the DB, image, static and
template folders now honour environment variables so they can live in writable
phone storage. Everything else is your original code.

---

## Building the APK (GitHub does it for you)

No Android tools are needed on your PC. Same flow as the step-counter app.

1. Create a free, empty GitHub repo (e.g. `hector-android`).
2. From inside this `HectorAndroid` folder:
   ```bash
   git init && git add . && git commit -m "Hector for Android" && git branch -M main && git remote add origin https://github.com/YOURNAME/hector-android.git && git push -u origin main
   ```
3. GitHub → **Actions** tab → wait for **Build APK** to finish (this one takes
   longer — ~5–10 min — because it downloads Python + Flask).
4. Open the run → **Artifacts** → download **Hector-apk** → unzip → `app-debug.apk`.
5. Copy to phone, install (allow "unknown apps"), open, grant **Physical
   activity** + **Notifications**.

> This is an ambitious build (embedded Python on Android). If the first Actions
> run fails, open the failed step, copy the red error text, and send it over —
> these are almost always a version-compatibility line to nudge, fixed in a
> quick follow-up push.

## Notes / known rough edges to revisit
- The Settings page still shows a Garmin card (now inert/"unavailable"); it can
  be hidden in a follow-up.
- The UI is your desktop layout in a phone-sized WebView — functional, pinch to
  zoom. A touch-optimised pass is a possible next step.
- APK is a debug build, ~30–50 MB (Python runtime + your ingredient photos).
