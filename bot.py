"""
Telegram Referral Bot
----------------------
What it does:
1. User sends /start -> bot asks them to join a list of required channels.
2. Once they've joined all of them, bot creates a UNIQUE invite link just for them
   and tells them to invite people to the MAIN channel using that link.
3. When someone new joins the MAIN channel through that link, the bot checks:
     - Has this person EVER been in the channel before (including if they left)?
       - If yes -> NOT counted (not a genuinely new person).
       - If no  -> counted, referrer's progress goes up.
4. When the referrer reaches INVITES_NEEDED, the bot congratulates them.

You only need to edit the .env file (channel IDs, bot token) - not this file.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# 1. LOAD SETTINGS FROM .env
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHANNEL_ID = os.getenv("MAIN_CHANNEL_ID")          # e.g. -1001234567890
REQUIRED_CHANNELS = [
    c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()
]  # e.g. @channel1,@channel2  (can be @username or -100... id)
INVITES_NEEDED = int(os.getenv("INVITES_NEEDED", "2"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

MEMBER_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
LEFT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 2. DATABASE (a tiny local file, no separate server needed)
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS members (
            user_id INTEGER PRIMARY KEY,
            first_seen_at TEXT,
            last_status TEXT,
            last_status_at TEXT
        );

        CREATE TABLE IF NOT EXISTS invite_links (
            user_id INTEGER PRIMARY KEY,
            link TEXT UNIQUE,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS referral_counts (
            user_id INTEGER PRIMARY KEY,
            count INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def member_exists(user_id: int) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM members WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def upsert_member(user_id: int, status: str):
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM members WHERE user_id = ?", (user_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE members SET last_status = ?, last_status_at = ? WHERE user_id = ?",
            (status, now_iso(), user_id),
        )
    else:
        conn.execute(
            "INSERT INTO members (user_id, first_seen_at, last_status, last_status_at) VALUES (?, ?, ?, ?)",
            (user_id, now_iso(), status, now_iso()),
        )
    conn.commit()
    conn.close()


def get_invite_link(user_id: int):
    conn = get_db()
    row = conn.execute("SELECT link FROM invite_links WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["link"] if row else None


def save_invite_link(user_id: int, link: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO invite_links (user_id, link, created_at) VALUES (?, ?, ?)",
        (user_id, link, now_iso()),
    )
    conn.commit()
    conn.close()


def get_owner_by_link(link: str):
    conn = get_db()
    row = conn.execute("SELECT user_id FROM invite_links WHERE link = ?", (link,)).fetchone()
    conn.close()
    return row["user_id"] if row else None


def get_referral_count(user_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT count FROM referral_counts WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["count"] if row else 0


def increment_referral_count(user_id: int) -> int:
    conn = get_db()
    conn.execute(
        "INSERT INTO referral_counts (user_id, count) VALUES (?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET count = count + 1",
        (user_id,),
    )
    conn.commit()
    new_count = conn.execute(
        "SELECT count FROM referral_counts WHERE user_id = ?", (user_id,)
    ).fetchone()["count"]
    conn.close()
    return new_count


# ---------------------------------------------------------------------------
# 3. HELPER: check if a user has joined ALL required channels
# ---------------------------------------------------------------------------
async def get_unjoined_channels(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    unjoined = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in MEMBER_STATUSES:
                unjoined.append(channel)
        except Exception as e:
            logger.warning(f"Could not check {channel} for user {user_id}: {e}")
            unjoined.append(channel)
    return unjoined


def join_keyboard(unjoined_channels):
    buttons = []
    for ch in unjoined_channels:
        url = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(f"Join {ch}", url=url)])
    buttons.append([InlineKeyboardButton("✅ I've joined all", callback_data="check_membership")])
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# 4. COMMAND: /start
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    unjoined = await get_unjoined_channels(context, user.id)

    if unjoined:
        await update.message.reply_text(
            f"Hello, {user.first_name}! 👋\n\n"
            f"Before you get your personal invite link, please join these channels:",
            reply_markup=join_keyboard(unjoined),
        )
        return

    await send_link_and_progress(update.effective_chat.id, user.id, context)


# ---------------------------------------------------------------------------
# 5. BUTTON: "I've joined all" -> re-check membership
# ---------------------------------------------------------------------------
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()

    unjoined = await get_unjoined_channels(context, user.id)
    if unjoined:
        await query.edit_message_text(
            "You haven't joined all the channels yet. Please join these and try again:",
            reply_markup=join_keyboard(unjoined),
        )
        return

    await query.edit_message_text("✅ Great, you're in! Here is your invite link:")
    await send_link_and_progress(update.effective_chat.id, user.id, context)


# ---------------------------------------------------------------------------
# 6. Create/send the user's personal invite link + their current progress
# ---------------------------------------------------------------------------
async def send_link_and_progress(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    link = get_invite_link(user_id)
    if not link:
        invite = await context.bot.create_chat_invite_link(
            chat_id=MAIN_CHANNEL_ID,
            name=f"ref_{user_id}",
        )
        link = invite.invite_link
        save_invite_link(user_id, link)

    count = get_referral_count(user_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔗 Your personal invite link:\n{link}\n\n"
            f"Progress: {count}/{INVITES_NEEDED} people invited.\n"
            f"Only NEW people (never in the channel before) count."
        ),
    )


# ---------------------------------------------------------------------------
# 7. Track joins/leaves in the MAIN channel
# ---------------------------------------------------------------------------
async def track_main_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if str(cmu.chat.id) != str(MAIN_CHANNEL_ID):
        return  # ignore updates from other chats

    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    joined_user = cmu.new_chat_member.user

    joined_now = new_status in MEMBER_STATUSES and old_status not in MEMBER_STATUSES
    left_now = new_status in LEFT_STATUSES

    if joined_now:
        is_new_person = not member_exists(joined_user.id)
        upsert_member(joined_user.id, new_status)

        invite_link_obj = cmu.invite_link
        if is_new_person and invite_link_obj:
            owner_id = get_owner_by_link(invite_link_obj.invite_link)
            if owner_id and owner_id != joined_user.id:
                new_count = increment_referral_count(owner_id)
                try:
                    if new_count >= INVITES_NEEDED:
                        text = (
                            f"🎉 You reached your goal! {new_count}/{INVITES_NEEDED} "
                            f"new people joined through your link."
                        )
                    else:
                        text = f"✅ A new person joined via your link! Progress: {new_count}/{INVITES_NEEDED}."
                    await context.bot.send_message(chat_id=owner_id, text=text)
                except Exception as e:
                    logger.warning(f"Could not notify referrer {owner_id}: {e}")

    elif left_now:
        upsert_member(joined_user.id, new_status)


# ---------------------------------------------------------------------------
# 8. MAIN ENTRY POINT
# ---------------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your .env file.")
    if not MAIN_CHANNEL_ID:
        raise RuntimeError("MAIN_CHANNEL_ID is missing. Set it in your .env file.")
    if not REQUIRED_CHANNELS:
        raise RuntimeError("REQUIRED_CHANNELS is missing. Set it in your .env file.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_membership, pattern="^check_membership$"))
    app.add_handler(ChatMemberHandler(track_main_channel, ChatMemberHandler.CHAT_MEMBER))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
