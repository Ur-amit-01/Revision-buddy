import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import random
from config import API_ID, API_HASH, BOT_TOKEN, MONGO_URI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MongoDB setup with error handling
try:
    db_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db_client.admin.command('ping')  # Test connection
    db = db_client.get_database("neet_revision_bot")
    users = db.users
    topics = db.topics
    logger.info("✅ MongoDB connected successfully")
except PyMongoError as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    raise SystemExit("Database connection failed")

# Constants with type hints
NEET_DATE: datetime = datetime(2026, 5, 5)
EBINGHAUS_INTERVALS: List[int] = [1, 3, 7, 15, 30]
TOXIC_MESSAGES: List[str] = [
    "Your competition just finished 10 revisions. You? Still here?",
    "At this rate, even bacteria evolve faster than your preparation.",
    "Procrastination is the art of keeping up with yesterday."
]

class BotUtils:
    """Utility class for common bot functions"""
    
    @staticmethod
    def get_user(user_id: int) -> Dict:
        """Get or create user with proper typing"""
        try:
            user = users.find_one({"_id": user_id}) or {
                "_id": user_id,
                "streak": 0,
                "last_study": None,
                "toxic_mode": False,
                "created_at": datetime.now()
            }
            users.update_one({"_id": user_id}, {"$setOnInsert": user}, upsert=True)
            return user
        except PyMongoError as e:
            logger.error(f"User fetch error: {e}")
            return {}

    @staticmethod
    def calculate_revision_dates() -> List[datetime]:
        """Calculate Ebbinghaus intervals with timezone awareness"""
        return [datetime.now() + timedelta(days=days) for days in EBINGHAUS_INTERVALS]

# Initialize Pyrogram client with better config
app = Client(
    "neet_revision_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,
    parse_mode=enums.ParseMode.MARKDOWN,
    sleep_threshold=30
)

# ======================== ENHANCED COMMAND HANDLERS ========================

@app.on_message(filters.command(["start", "help"]) & filters.private)
async def enhanced_start(client, message):
    """Improved start command with better formatting"""
    try:
        user = BotUtils.get_user(message.from_user.id)
        
        text = f"""
📚 **NEET Revision Bot**  
*Optimize your study using spaced repetition*

🔥 **Current Streak:** `{user.get('streak', 0)}` days  
💀 **Toxic Mode:** `{"ON" if user.get("toxic_mode") else "OFF"}`  

**Available Commands:**  
▸ /studied `<topic>` - Log new study material  
▸ /myprogress - View revision calendar  
▸ /neetdays - Countdown to NEET {NEET_DATE.year}  
▸ /toggletoxic - Toggle savage motivation  
        """
        
        await message.reply_text(
            text.strip(),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Quick Log", callback_data="log_quick")],
                [InlineKeyboardButton("📊 View Progress", callback_data="view_progress")]
            ])
        )
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.reply_text("⚠️ An error occurred. Please try again.")

@app.on_message(filters.command("studied") & filters.private)
async def enhanced_log_study(client, message):
    """Improved study logging with validation"""
    try:
        if len(message.command) < 2:
            return await message.reply("Please specify a topic!\nExample: `/studied Plant Physiology`")
        
        topic = " ".join(message.command[1:])
        user_id = message.from_user.id
        
        # Update user data
        streak = update_streak(user_id)
        schedule_revisions(user_id, topic)
        
        # Prepare response
        user = BotUtils.get_user(user_id)
        revision_dates = "\n".join(
            f"▸ {days} day(s) - {(datetime.now() + timedelta(days=days)).strftime('%b %d')}"
            for days in EBINGHAUS_INTERVALS[:3]
        )
        
        text = f"""
✅ **Topic Logged:** `{topic}`  
🗓 **Revision Schedule:**  
{revision_dates}  

🔥 **Current Streak:** `{streak}` days  
        """
        
        if user.get("toxic_mode") and streak < 3:
            text += f"\n\n💀 *{random.choice(TOXIC_MESSAGES)}*"
        
        await message.reply_text(
            text.strip(),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✔️ Mark Done", callback_data=f"done_{topic}"),
                    InlineKeyboardButton("⏸ Snooze", callback_data="snooze_1d")
                ]
            ])
        )
    except Exception as e:
        logger.error(f"Study log error: {e}")
        await message.reply_text("⚠️ Failed to log your study. Please try again.")

# ======================== ENHANCED CALLBACK HANDLERS ========================

@app.on_callback_query(filters.regex(r"^done_(.+)"))
async def enhanced_mark_done(client, callback):
    """Improved revision completion handler"""
    try:
        topic = callback.matches[0].group(1)
        user_id = callback.from_user.id
        
        topics.update_one(
            {"user_id": user_id, "topic": topic},
            {"$push": {"completed_revisions": datetime.now()}}
        )
        
        await callback.answer("✅ Revision completed!")
        await callback.message.edit_text(
            f"🎉 **Revision Marked Complete**\n`{topic}`\n\n"
            "I'll remind you for the next interval!",
            reply_markup=None
        )
    except Exception as e:
        logger.error(f"Mark done error: {e}")
        await callback.answer("⚠️ Failed to update. Try again.", show_alert=True)

# ======================== NEW FEATURES ========================

@app.on_message(filters.command("myprogress") & filters.private)
async def view_progress(client, message):
    """New feature: Visual progress tracker"""
    try:
        user_topics = list(topics.find({"user_id": message.from_user.id}))
        
        if not user_topics:
            return await message.reply("You haven't logged any topics yet!")
        
        progress_text = ["📊 **Your Revision Progress**\n"]
        
        for topic in user_topics[:10]:  # Limit to 10 topics for readability
            completed = len(topic.get("completed_revisions", []))
            total = len(EBINGHAUS_INTERVALS)
            progress = f"`{topic['topic']}`: {completed}/{total} revisions"
            
            # Add progress bar
            bar = "[" + "■" * completed + "□" * (total - completed) + "]"
            progress_text.append(f"{progress} {bar}")
        
        await message.reply_text("\n".join(progress_text))
    except Exception as e:
        logger.error(f"Progress error: {e}")
        await message.reply_text("⚠️ Couldn't fetch your progress. Try again later.")

# ======================== IMPROVED BACKGROUND TASKS ========================

async def smart_reminder_task():
    """Enhanced reminder system with rate limiting"""
    while True:
        try:
            now = datetime.now()
            due_topics = topics.find({
                "revisions": {"$lte": now},
                "next_reminder": {"$lt": now}  # Prevent spam
            })
            
            for topic in due_topics:
                user = BotUtils.get_user(topic["user_id"])
                
                # Build reminder message
                msg = [
                    f"📌 **Revision Due:** `{topic['topic']}`",
                    f"⏳ **Interval:** Day {EBINGHAUS_INTERVALS[len(topic.get('completed_revisions', []))]}",
                    f"🔥 **Streak:** {user.get('streak', 0)} days"
                ]
                
                if user.get("toxic_mode"):
                    msg.append(f"\n💀 *{random.choice(TOXIC_MESSAGES)}*")
                
                # Send reminder
                await app.send_message(
                    topic["user_id"],
                    "\n".join(msg),
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✔️ Done", callback_data=f"done_{topic['topic']}"),
                            InlineKeyboardButton("⏸ Snooze", callback_data="snooze_1d")
                        ]
                    ])
                )
                
                # Update next reminder time
                topics.update_one(
                    {"_id": topic["_id"]},
                    {"$set": {"next_reminder": now + timedelta(hours=6)}}
                )
            
            await asyncio.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Reminder task error: {e}")
            await asyncio.sleep(300)  # Wait 5 min on error

# ======================== BOT LIFECYCLE MANAGEMENT ========================

@app.on_startup()
async def startup_task():
    """Initialize background tasks"""
    app.loop.create_task(smart_reminder_task())
    logger.info("Background tasks initialized")

if __name__ == "__main__":
    logger.info("🚀 Starting NEET Revision Bot")
    app.run()
