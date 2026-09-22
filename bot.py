import re
import sqlite3
import uuid
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import Forbidden
import config

# ----------------- DATABASE SETUP ----------------- #
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            real_id INTEGER PRIMARY KEY,
            mask_id TEXT UNIQUE,
            username TEXT,
            is_banned INTEGER DEFAULT 0
        )
    """)
    
    # Safely migrate existing databases missing the username column
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "username" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mask_id TEXT,
            message_text TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def update_and_get_user(user):
    """Fetches the user's mask, and updates their username in case they changed it."""
    real_id = user.id
    # Normalize username to lowercase without the @ symbol for easy searching
    username = user.username.lower() if user.username else None
    
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT mask_id, is_banned FROM users WHERE real_id = ?", (real_id,))
    row = cursor.fetchone()
    
    if row:
        mask_id, is_banned = row[0], bool(row[1])
        # Update username just in case they changed it in Telegram settings
        cursor.execute("UPDATE users SET username = ? WHERE real_id = ?", (username, real_id))
        conn.commit()
        conn.close()
        return mask_id, is_banned
    
    # First time user: Create a new mask
    mask_id = f"Mask #{uuid.uuid4().hex[:4].upper()}"
    cursor.execute("INSERT INTO users (real_id, mask_id, username) VALUES (?, ?, ?)", (real_id, mask_id, username))
    conn.commit()
    conn.close()
    return mask_id, False

def get_real_id_by_username(username: str):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    # Strip @ and lower it to match our database format
    clean_username = username.replace("@", "").lower()
    cursor.execute("SELECT real_id FROM users WHERE username = ?", (clean_username,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_real_id_by_mask(mask_id: str):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT real_id FROM users WHERE mask_id = ?", (mask_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# ----------------- COMMAND HANDLERS ----------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mask_id, is_banned = update_and_get_user(update.effective_user)
    
    if is_banned:
        await update.message.reply_text("You have been banned from using this service.")
        return

    await update.message.reply_text(
        f"👋 Welcome to John Doe Confessions.\n"
        f"Your masked ID: `{mask_id}`\n\n"
        f"**Available Commands:**\n"
        f"📢 `/confess <text>` — Submit a public confession to the channel\n"
        f"✉️ `/msg @username <text>` — Send an anonymous message to someone in the directory",
        parse_mode="Markdown"
    )

# ----------------- PUBLIC CONFESSIONS (INSTANT POST) ----------------- #
async def confess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mask_id, is_banned = update_and_get_user(update.effective_user)

    if is_banned:
        return

    confession_text = " ".join(context.args)
    if not confession_text:
        await update.message.reply_text("Usage: /confess <your message here>")
        return

    try:
        # Instantly post to the channel with custom header
        posted_msg = await context.bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=f"🚨 **NEW CONFESSION**  🚨\n\n{confession_text}",
            parse_mode="Markdown"
        )
        
        # Send receipt to user
        await update.message.reply_text("✅ Your confession has been posted to the channel.")
        
        # Send the Message ID to the admin for replying
        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=f"🔔 Confession auto-posted.\nTo reply publicly as John Doe, copy/paste this:\n\n`/reply {posted_msg.message_id} Your reply here`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to post to channel. Ensure the bot is an admin.")
        print(f"Error posting: {e}")

# ----------------- ADMIN DIRECT REPLY ----------------- #
async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.ADMIN_CHAT_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply <message_id> <your response>")
        return

    try:
        msg_id = int(context.args[0])
        reply_text = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("❌ The message ID must be a number.")
        return

    try:
        # Posts with custom header
        await context.bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=f"🕵️ **John Doe replies:** 🕵️\n\n{reply_text}",
            reply_to_message_id=msg_id,
            parse_mode="Markdown"
        )
        await update.message.reply_text("✅ Reply successfully posted to the channel.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to reply. Is the message ID correct? Error: {e}")

# ----------------- PRIVATE MESSAGING DIRECTORY ----------------- #
async def send_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_mask, is_banned = update_and_get_user(update.effective_user)
    if is_banned:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/msg @username Hey there!`", parse_mode="Markdown")
        return

    target_username = context.args[0]
    message_text = " ".join(context.args[1:])
    
    target_real_id = get_real_id_by_username(target_username)
    
    if not target_real_id:
        await update.message.reply_text(
            f"❌ User {target_username} not found.\nThey must start this bot before you can message them."
        )
        return
        
    if target_real_id == update.effective_user.id:
        await update.message.reply_text("You cannot message yourself.")
        return

    try:
        await context.bot.send_message(
            chat_id=target_real_id,
            text=f"📨 **Message from {sender_mask}:**\n\n{message_text}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Anonymous message delivered to {target_username}.")
    except Forbidden:
        await update.message.reply_text(f"❌ {target_username} has blocked the bot and cannot receive messages.")

# ----------------- NATIVE REPLY ROUTER ----------------- #
async def route_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_mask, is_banned = update_and_get_user(update.effective_user)
    if is_banned:
        return

    # Only process if the user is using Telegram's native "Reply" feature
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "To send a private message, use `/msg @username <text>`.\n"
            "To reply to someone who messaged you, swipe right on their message bubble to reply directly.",
            parse_mode="Markdown"
        )
        return

    original_text = update.message.reply_to_message.text
    if not original_text:
        return

    # Look for "Mask #XXXX" in the message they are replying to
    match = re.search(r"Mask #[A-F0-9]{4}", original_text)
    if not match:
        await update.message.reply_text("❌ Could not identify the original sender from that message.")
        return

    target_mask = match.group(0)
    target_real_id = get_real_id_by_mask(target_mask)

    if not target_real_id:
        await update.message.reply_text("❌ Sender no longer exists in the database.")
        return

    try:
        await context.bot.send_message(
            chat_id=target_real_id,
            text=f"📨 **Message from {sender_mask}:**\n\n{update.message.text}",
            parse_mode="Markdown"
        )
        await update.message.reply_text("✅ Reply delivered.")
    except Forbidden:
        await update.message.reply_text("❌ That user has blocked the bot and cannot receive your reply.")

# ----------------- MAIN ENTRYPOINT ----------------- #
def main():
    init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confess", confess))
    app.add_handler(CommandHandler("reply", admin_reply))
    app.add_handler(CommandHandler("msg", send_private_message))

    # Catch all normal text messages to handle native replies
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_replies))

    print("Bot is listening locally...")
    app.run_polling()

if __name__ == "__main__":
    main()