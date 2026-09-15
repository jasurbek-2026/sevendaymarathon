"""
Telegram Referral Bot
----------------------
What it does:
1. User sends /start -> bot sends an intro message, then (if not already a member
   of the main public channel) shows a photo + caption asking them to join it.
2. Once they've joined the main channel, bot creates a UNIQUE invite link
   just for them and tells them to invite people to the MAIN channel using it.
3. When someone new joins the MAIN channel through that link, the bot checks:
     - Has this person EVER been in the channel before (including if they left)?
       - If yes -> NOT counted (not a genuinely new person).
       - If no  -> counted, referrer's progress goes up.
4. As the referrer's progress increases, the bot sends progress updates.
5. When the referrer reaches INVITES_NEEDED, the bot sends them a one-time
   invite link to the CLOSED channel.

You only need to edit the .env file (channel IDs, bot token, photo path) - not this file.
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
MAIN_CHANNEL_ID = os.getenv("MAIN_CHANNEL_ID")          # e.g. -1001234567890 (public channel, numeric ID)
MAIN_CHANNEL_USERNAME = os.getenv("MAIN_CHANNEL_USERNAME")  # e.g. @your_main_channel - used for the join button link
CLOSED_CHANNEL_ID = os.getenv("CLOSED_CHANNEL_ID")      # e.g. -1009876543210 (closed marathon channel)
INVITES_NEEDED = int(os.getenv("INVITES_NEEDED", "2"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
RESULT_PHOTO_PATH = os.getenv("RESULT_PHOTO_PATH", "result.jpg")  # local path to the IELTS result photo

MEMBER_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
LEFT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TEXT CONTENT (edit wording here if needed)
# ---------------------------------------------------------------------------
INTRO_TEXT = (
    "Assalamu alaykum. Buni Diqqat bilan o'qing!\n\n"
    "Siz bu botni 7-kunlik Reading Marathonga qo'shilish uchun ishlatyapsiz.\n\n"
    "Yopiq kanalga bir martalik link olish uchun, siz 2 ta intermediate va undan "
    "yuqori darajadagi do'stingizni taklif qilishingiz kerak. Sizga buning uchun "
    "maxsus link beriladi.\n\n"
    "Startni bosing, kanalimga qo'shilmagan bo'lsangiz qo'shiling, va odam taklif "
    "qilib, marathonga ulgurib qoling."
)

JOIN_CAPTION = (
    "Assalomu alaykum.\n\n"
    "Ismim Jasurbek Abdullayev, IELTS natijam 8.5.\n\n"
    "Readingdan natijam 9.0. Sizga bu 7 kunlik marafonda katta yordam bera olaman "
    "deb umid qilib shu botni ishga tushirdim.\n\n"
    "Kanalimda yo'q ekansiz. Qo'shilib oling, so'ng sizga alohida link beriladi. "
    "U orqali ikkita do'stingizni shu asosiy kanalimga taklif qiling, va maxsus "
    "yopiq kanal linkini olasiz.\n\n"
    "Marfon 23-Sentabrda boshlanadi. Ungacha kanaldagi kerakli material va "
    "postlardan foydalansangiz bo'ladi.\n\n"
    "Darslarni ingliz tilida o'taman. Shunga ham tayyor turing. Record qilib "
    "olinadi, agar tushunmay qolsangiz, yoki internet muammo bo'lsa, keyinchalik "
    "ko'rib olaverasiz.\n\n"
    "Omad tilayman!"
)

MSG_PROGRESS_ONE = (
    "Kanalimga 1 ta odamni taklif qildingiz. Rahmat sizga. Endi esa ikkinchi "
    "odamni taklif qilib, yopiq kanalga tezroq qo'shilib oling."
)


def progress_message(count: int) -> str:
    """Text sent for any progress count that isn't the final goal."""
    if count == 1:
        return MSG_PROGRESS_ONE
    remaining = INVITES_NEEDED - count
    return (
        f"Kanalimga {count} ta odamni taklif qildingiz. Rahmat sizga. "
        f"Yana {remaining} ta do'stingizni taklif qiling."
    )


def goal_reached_message(count: int, link: str) -> str:
    return (
        f"Tabriklayman. {count}-odamni ham qo'shib oldingiz-a! "
        f"Mana linkingiz oling: {link}"
    )


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

        CREATE TABLE IF NOT EXISTS closed_invite_links (
            user_id INTEGER PRIMARY KEY,
            link TEXT UNIQUE,
            created_at TEXT
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


def get_closed_invite_link(user_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT link FROM closed_invite_links WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["link"] if row else None


def save_closed_invite_link(user_id: int, link: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO closed_invite_links (user_id, link, created_at) VALUES (?, ?, ?)",
        (user_id, link, now_iso()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 3. HELPER: check if the user has joined the main channel
# ---------------------------------------------------------------------------
async def is_main_channel_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=MAIN_CHANNEL_ID, user_id=user_id)
        return member.status in MEMBER_STATUSES
    except Exception as e:
        logger.warning(f"Could not check main channel membership for user {user_id}: {e}")
        return False


def join_keyboard():
    """
    NOTE on styling: Telegram's Bot API does not support custom button colors
    (no "glass"/green-white styling) for regular inline keyboard buttons - their
    look always follows the user's Telegram theme. The only way to get a truly
    custom-styled (glassmorphism, custom colors, etc.) button is a Telegram
    Web App / Mini App button rendered with your own HTML/CSS, which is a
    separate, bigger build. Below uses a plain inline button with emoji so it at
    least reads as a distinct, "join" call to action.
    """
    url = f"https://t.me/{MAIN_CHANNEL_USERNAME.lstrip('@')}"
    buttons = [
        [InlineKeyboardButton("🟢 Kanalga qo'shilish", url=url)],
        [InlineKeyboardButton("✅ Tekshirish", callback_data="check_membership")],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# 4. COMMAND: /start
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    # 1) Intro message, always sent first.
    await context.bot.send_message(chat_id=chat_id, text=INTRO_TEXT)

    # 2) Only show the "please join my channel" prompt if they're not a member yet.
    if not await is_main_channel_member(context, user.id):
        await send_join_prompt(chat_id, context)
        return

    await send_link_and_progress(chat_id, user.id, context)


async def send_join_prompt(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    with open(RESULT_PHOTO_PATH, "rb") as photo:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=JOIN_CAPTION,
            reply_markup=join_keyboard(),
        )


# ---------------------------------------------------------------------------
# 5. BUTTON: "✅ Tekshirish" -> re-check membership
# ---------------------------------------------------------------------------
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()

    if not await is_main_channel_member(context, user.id):
        # Still not joined -> resend the exact same message/buttons.
        await query.edit_message_caption(
            caption=JOIN_CAPTION,
            reply_markup=join_keyboard(),
        )
        return

    await query.edit_message_caption(caption="✅ Great, you're in! Here is your invite link:")
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
async def create_closed_channel_link(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    """One-time (single-use) invite link to the closed marathon channel."""
    link = get_closed_invite_link(user_id)
    if link:
        return link
    invite = await context.bot.create_chat_invite_link(
        chat_id=CLOSED_CHANNEL_ID,
        name=f"closed_{user_id}",
        member_limit=1,
    )
    save_closed_invite_link(user_id, invite.invite_link)
    return invite.invite_link


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
                        closed_link = await create_closed_channel_link(owner_id, context)
                        text = goal_reached_message(new_count, closed_link)
                    else:
                        text = progress_message(new_count)
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
    if not CLOSED_CHANNEL_ID:
        raise RuntimeError("CLOSED_CHANNEL_ID is missing. Set it in your .env file.")
    if not MAIN_CHANNEL_USERNAME:
        raise RuntimeError("MAIN_CHANNEL_USERNAME is missing. Set it in your .env file.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_membership, pattern="^check_membership$"))
    app.add_handler(ChatMemberHandler(track_main_channel, ChatMemberHandler.CHAT_MEMBER))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
