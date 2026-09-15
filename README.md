# Referral Bot — Setup Guide

You need these files (all included):
- `bot.py` — the bot itself. You don't need to edit this.
- `requirements.txt` — tells the computer which libraries to install.
- `.env` — your secret settings (already filled in).
- `.gitignore` — tells Git which files to NEVER upload (like your bot token).
- `README.md` — this guide.
- `results.jpg` — the photo sent in the join prompt.

## Step 1 — Make your bot an admin

In Telegram, go to both channels (the main channel + the closed channel) →
Administrators → Add Admin → add your bot. It needs admin rights to check
who's a member and to create invite links.

## Step 2 — Get your channel IDs

- For the **main** channel's join-button link, you need its `@username`
  (this goes in `MAIN_CHANNEL_USERNAME`).
- For both `MAIN_CHANNEL_ID` and `CLOSED_CHANNEL_ID`, you need the numeric
  chat ID (looks like `-1001234567890`), even for public channels.
  Easiest way: forward any message from that channel to **@userinfobot** or
  **@RawDataBot** — it will show you the chat ID.

## Step 3 — Check your settings

Open `.env` in VS Code and confirm it has:
- `BOT_TOKEN` — from @BotFather
- `MAIN_CHANNEL_ID` — numeric ID of the main channel
- `MAIN_CHANNEL_USERNAME` — the main channel's `@username`, used for the join button
- `CLOSED_CHANNEL_ID` — numeric ID of the closed channel people unlock
- `INVITES_NEEDED` — how many new people someone must invite (default 2)
- `RESULT_PHOTO_PATH` — path to the join-prompt photo (must match the actual filename, e.g. `results.jpg`)

## Step 4 — Test it on your own computer (optional but recommended)

Open a terminal in VS Code (Terminal → New Terminal) and run: