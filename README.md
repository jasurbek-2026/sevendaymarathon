# Referral Bot — Setup Guide

You need exactly these 5 files (all included):
- `bot.py` — the bot itself. You don't need to edit this.
- `requirements.txt` — tells the computer which libraries to install.
- `.env.example` — a template for your secret settings.
- `.gitignore` — tells Git which files to NEVER upload (like your bot token).
- `README.md` — this guide.

## Step 1 — Make your bot an admin

In Telegram, go to each channel (the main one + all required ones) →
Administrators → Add Admin → add your bot. It needs admin rights to check
who's a member and to create invite links.

## Step 2 — Get your channel IDs

- For a **public** channel, the ID is just `@channelusername`.
- For a **private** channel, you need the numeric ID (looks like `-1001234567890`).
  Easiest way: forward any message from that channel to the bot
  **@userinfobot** or **@RawDataBot** — it will show you the chat ID.

## Step 3 — Fill in your settings

1. Copy `.env.example` and rename the copy to `.env`.
2. Open `.env` in VS Code and fill in:
   - `BOT_TOKEN` — from @BotFather
   - `MAIN_CHANNEL_ID` — your growth channel's ID
   - `REQUIRED_CHANNELS` — comma-separated list, e.g. `@channel1,@channel2`

## Step 4 — Test it on your own computer (optional but recommended)

Open a terminal in VS Code (Terminal → New Terminal) and run:

```
pip install -r requirements.txt
python bot.py
```

Then message your bot on Telegram and send `/start`. If it replies, it works.
Press `Ctrl+C` in the terminal to stop it.

## Step 5 — Push to GitHub

```
git init
git add .
git commit -m "First version of referral bot"
git branch -M main
git remote add origin <your-empty-repo-URL>
git push -u origin main
```

(`.env` will NOT be uploaded — that's what `.gitignore` is for. Good, because
it has your secret token.)

## Step 6 — Deploy on Railway

1. Railway → New Project → Deploy from GitHub repo → pick this repo.
2. Go to the project's **Variables** tab and add the same values from your
   `.env` file one by one (`BOT_TOKEN`, `MAIN_CHANNEL_ID`, `REQUIRED_CHANNELS`,
   `INVITES_NEEDED`).
3. Under **Settings**, pick at least the **$7/month** plan (free tier sleeps,
   which breaks the bot).
4. Railway will detect it's a Python app and run `python bot.py` automatically
   (it reads `requirements.txt` for you). Check the **Deploy Logs** tab —
   you should see `Bot starting...`.

## How the "no cheating" logic works (in plain terms)

- Every person who's ever been seen in your main channel is remembered in a
  small local database.
- When someone joins through a referral link, the bot checks: "Have I ever
  seen this person in the channel before (even if they later left)?"
  - Never seen before → it's a genuinely new person → counted.
  - Seen before (including someone who left and came back) → NOT counted.

If later you want to *allow* people who left a long time ago to count again,
tell me and I'll add a time window (e.g. "only exclude leavers from the last
30 days") — it's a small change.
