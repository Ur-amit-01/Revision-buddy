import os
from datetime import datetime, timedelta
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from dateutil.relativedelta import relativedelta
import random
from config import *

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")  # Get free cluster from MongoDB Atlas
db_client = MongoClient(mongo_uri)
db = db_client["neet_revision_bot"]
users = db["users"]
topics = db["topics"]

# Bot setup
app = Client(
    "neet_revision_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

# Constants
NEET_DATE = datetime(2026, 5, 5)  # Change to actual NEET date
EBINGHAUS_INTERVALS = [1, 3, 7, 15, 30]  # Days between revisions

# Toxic motivation messages
TOXIC_MESSAGES = [
    "Your competition just finished 10 revisions. You? Still here?",
    "At this rate, even bacteria evolve faster than your preparation.",
    "Procrastination is the art of keeping up with yesterday."
]

# ======================== CORE FUNCTIONS ========================

def get_user(user_id):
    """Get or create user in MongoDB"""
    user = users.find_one({"_id": user_id})
    if not user:
        user = {
            "_id": user_id,
            "streak": 0,
            "last_study": None,
            "toxic_mode": False
        }
        users.insert_one(user)
    return user

def update_streak(user_id):
    """Update user's study streak"""
    user = get_user(user_id)
    today = datetime.now().date()
    
    if user["last_study"]:
        last_study = user["last_study"].date()
        if today == last_study:
            return user["streak"]
        elif today == last_study + timedelta(days=1):
            new_streak = user["streak"] + 1
        else:
            new_streak = 1  # Reset streak
    else:
        new_streak = 1
    
    users.update_one(
        {"_id": user_id},
        {"$set": {"streak": new_streak, "last_study": datetime.now()}}
    )
    return new_streak

def schedule_revisions(user_id, topic_name):
    """Create Ebbinghaus revision schedule"""
    revision_dates = [
        datetime.now() + timedelta(days=days) 
        for days in EBINGHAUS_INTERVALS
    ]
    
    topics.insert_one({
        "user_id": user_id,
        "topic": topic_name,
        "created_at": datetime.now(),
        "revisions": revision_dates,
        "completed_revisions": []
    })

# ======================== COMMAND HANDLERS ========================

@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    user = get_user(message.from_user.id)
    toxic_mode = "🟢 ON" if user.get("toxic_mode") else "🔴 OFF"
    
    await message.reply_text(
        f"""📚 **NEET Revision Bot** (Beta)
        
Log your study topics and I'll remind you to revise them based on the Ebbinghaus forgetting curve.

🔥 **Current Streak:** {user.get("streak", 0)} days
💀 **Toxic Mode:** {toxic_mode}

**Commands:**
/studied [topic] - Log a new topic
/myprogress - Show revision schedule
/neetdays - Days remaining until NEET
/toggletoxic - Enable savage motivation""",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Log Revision", callback_data="log_quick")]
        ])
    )

@app.on_message(filters.command("studied"))
async def log_study(client, message):
    if len(message.command) < 2:
        return await message.reply("Please specify a topic!\nExample: `/studied Plant Physiology`")
    
    topic = " ".join(message.command[1:])
    user_id = message.from_user.id
    
    # Update streak
    streak = update_streak(user_id)
    
    # Schedule revisions
    schedule_revisions(user_id, topic)
    
    # Toxic mode check
    user = get_user(user_id)
    toxic_remark = ""
    if user.get("toxic_mode") and streak < 3:
        toxic_remark = f"\n\n💀 {random.choice(TOXIC_MESSAGES)}"
    
    await message.reply_text(
        f"""✅ **Topic logged:** {topic}
        
Next revisions will be due at:
- {EBINGHAUS_INTERVALS[0]} day(s)
- {EBINGHAUS_INTERVALS[1]} days
- {EBINGHAUS_INTERVALS[2]} days

🔥 **Current Streak:** {streak} days{toxic_remark}""",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Mark Revised", callback_data=f"done_{topic}"),
                InlineKeyboardButton("Snooze", callback_data="snooze_1d")
            ]
        ])
    )

@app.on_message(filters.command("neetdays"))
async def neet_countdown(client, message):
    days_left = (NEET_DATE - datetime.now()).days
    await message.reply_text(
        f"⏳ **{days_left} days left until NEET {NEET_DATE.year}**\n"
        f"That's {days_left*3} potential revisions per topic!"
    )

# ======================== CALLBACK HANDLERS ========================

@app.on_callback_query(filters.regex(r"^log_quick$"))
async def quick_log(client, callback):
    await callback.message.edit_text(
        "What did you study today? (Reply with topic)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel")]
        ])
    )

@app.on_callback_query(filters.regex(r"^done_(.+)"))
async def mark_done(client, callback):
    topic = callback.matches[0].group(1)
    user_id = callback.from_user.id
    
    # Update topic in database
    topics.update_one(
        {"user_id": user_id, "topic": topic},
        {"$push": {"completed_revisions": datetime.now()}}
    )
    
    await callback.message.edit_text(
        f"🎉 **Revision marked complete:** {topic}\n"
        "I'll remind you for the next interval!",
        reply_markup=None
    )

@app.on_callback_query(filters.regex(r"^snooze_(\d+[dh])"))
async def snooze(client, callback):
    period = callback.matches[0].group(1)
    await callback.message.edit_text(
        f"⏸ Revision snoozed for {period}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Wake Me Anyway", callback_data="unsnooze")]
        ])
    )

# ======================== TOXIC MODE ========================

@app.on_message(filters.command("toggletoxic"))
async def toggle_toxic(client, message):
    user_id = message.from_user.id
    user = get_user(user_id)
    new_mode = not user.get("toxic_mode", False)
    
    users.update_one(
        {"_id": user_id},
        {"$set": {"toxic_mode": new_mode}}
    )
    
    await message.reply_text(
        f"💀 **Toxic Mode {'ENABLED' if new_mode else 'DISABLED'}**\n"
        "Prepare for brutal honesty!" + (
        "\n\n" + random.choice(TOXIC_MESSAGES) if new_mode else ""
        )
    )

# ======================== DAILY CHECKER ========================

async def check_revisions():
    """Run daily to send reminders"""
    now = datetime.now()
    for topic in topics.find({"revisions": {"$lte": now}}):
        user = get_user(topic["user_id"])
        
        # Build message
        msg = f"📌 **Revision Due:** {topic['topic']}\n"
        msg += f"🔁 Interval: Day {EBINGHAUS_INTERVALS[len(topic['completed_revisions'])]}\n"
        
        if user.get("toxic_mode"):
            msg += f"\n💀 {random.choice(TOXIC_MESSAGES)}"
        
        # Send with action buttons
        await app.send_message(
            topic["user_id"],
            msg,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Done", callback_data=f"done_{topic['topic']}"),
                    InlineKeyboardButton("Snooze 1d", callback_data="snooze_1d")
                ]
            ])
        )

# ======================== RUN BOT ========================

if __name__ == "__main__":
    print("Starting NEET Revision Bot...")
    app.run()
