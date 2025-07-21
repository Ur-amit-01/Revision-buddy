import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from config import *
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup
)
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MongoDB setup
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]

# Collections
users_col = db["users"]
topics_col = db["topics"]
revisions_col = db["revisions"]

# Revision intervals (in days) based on Ebbinghaus curve
REVISION_INTERVALS = [0, 1, 3, 7, 15, 30]
SUBJECTS = ["Biology", "Chemistry", "Physics", "Other"]

# Telegram bot setup
app = Client(
    "ebbinghaus_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Helper functions
def get_user(user_id: int) -> Dict:
    """Get or create user in database"""
    user = users_col.find_one({"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "language": "en",
            "timezone": "UTC",
            "notification_time": "09:00",
            "state": None
        }
        users_col.insert_one(user)
    return user

def update_user_state(user_id: int, state: str, data: dict = None):
    """Update user state machine"""
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"state": state, "state_data": data or {}}}
    )

def create_topic(user_id: int, topic_name: str, subject: str, notes: str = "") -> Dict:
    """Create a new study topic with scheduled revisions"""
    topic = {
        "user_id": user_id,
        "topic_name": topic_name,
        "subject": subject,
        "notes": notes,
        "created_at": datetime.utcnow(),
        "is_active": True
    }
    topic_id = topics_col.insert_one(topic).inserted_id
    
    # Create revision schedule
    revisions = []
    for interval in REVISION_INTERVALS:
        due_date = datetime.utcnow() + timedelta(days=interval)
        revision = {
            "topic_id": topic_id,
            "user_id": user_id,
            "interval": interval,
            "due_date": due_date,
            "completed": False,
            "completed_at": None,
            "created_at": datetime.utcnow()
        }
        revisions.append(revision)
    
    revisions_col.insert_many(revisions)
    return topic

def get_due_revisions(user_id: int) -> List[Dict]:
    """Get all due revisions for a user"""
    now = datetime.utcnow()
    return list(revisions_col.find({
        "user_id": user_id,
        "completed": False,
        "due_date": {"$lte": now}
    }).sort("due_date", 1))

def complete_revision(revision_id) -> bool:
    """Mark a revision as completed"""
    result = revisions_col.update_one(
        {"_id": revision_id},
        {"$set": {
            "completed": True,
            "completed_at": datetime.utcnow()
        }}
    )
    return result.modified_count > 0

def get_user_topics(user_id: int) -> List[Dict]:
    """Get all active topics for a user"""
    return list(topics_col.find({
        "user_id": user_id,
        "is_active": True
    }).sort("created_at", -1))

def get_topic_revisions(topic_id) -> List[Dict]:
    """Get all revisions for a topic"""
    return list(revisions_col.find({
        "topic_id": topic_id
    }).sort("interval", 1))

def format_revision_date(dt: datetime) -> str:
    """Format datetime for display"""
    return dt.strftime("%b %d, %Y %H:%M")

def create_topic_keyboard():
    """Create inline keyboard for subject selection"""
    buttons = []
    for subject in SUBJECTS:
        buttons.append([InlineKeyboardButton(subject, callback_data=f"subject_{subject}")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def create_main_menu_keyboard():
    """Create reply keyboard for main menu"""
    return ReplyKeyboardMarkup(
        [
            ["➕ Add Topic", "📝 Due Revisions"],
            ["📚 My Topics", "📊 Statistics"],
            ["⚙️ Settings"]
        ],
        resize_keyboard=True
    )

# Bot commands
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    """Welcome message and instructions"""
    user = get_user(message.from_user.id)
    
    welcome_text = """
    🎓 *Ebbinghaus Revision Bot* 🎓

    I'll help you remember what you study using *spaced repetition* based on the Ebbinghaus Forgetting Curve.

    🔹 *How it works:*
    1. Add topics you study
    2. I'll schedule revisions at optimal intervals
    3. You'll get notifications when it's time to review
    4. Mark revisions as done

    📋 *Main Menu:*
    - Add Topic: Create new study material
    - Due Revisions: See what needs review
    - My Topics: View all your topics
    - Statistics: Track your progress
    - Settings: Configure notifications

    Use the buttons below or type commands like /add, /due, etc.
    """
    
    await message.reply_text(
        welcome_text,
        reply_markup=create_main_menu_keyboard(),
        disable_web_page_preview=True
    )

@app.on_message(filters.command("add") | filters.regex(r"^➕ Add Topic$"))
async def add_topic_start(client: Client, message: Message):
    """Start topic creation process"""
    user_id = message.from_user.id
    update_user_state(user_id, "awaiting_topic_name")
    
    await message.reply_text(
        "📝 Let's add a new topic!\n\n"
        "Please send me the name of the topic you want to study.\n"
        "Example: *Human Digestive System*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data="cancel")]
        ])
    )

@app.on_message(filters.text & filters.private & ~filters.command())
async def handle_text_input(client: Client, message: Message):
    """Handle text input based on user state"""
    user = get_user(message.from_user.id)
    
    if user.get("state") == "awaiting_topic_name":
        update_user_state(
            user["user_id"],
            "awaiting_topic_subject",
            {"topic_name": message.text}
        )
        await message.reply_text(
            f"📚 Topic: *{message.text}*\n\n"
            "Now select the subject:",
            reply_markup=create_topic_keyboard()
        )
    elif user.get("state") == "awaiting_topic_notes":
        topic_data = user.get("state_data", {})
        topic = create_topic(
            user["user_id"],
            topic_data["topic_name"],
            topic_data["subject"],
            message.text
        )
        update_user_state(user["user_id"], None)
        
        await message.reply_text(
            f"✅ *Topic added successfully!*\n\n"
            f"📖 *Topic:* {topic['topic_name']}\n"
            f"🔬 *Subject:* {topic['subject']}\n"
            f"📝 *Notes:* {topic['notes'][:100] + '...' if topic['notes'] else 'None'}\n\n"
            "Your revisions are scheduled at these intervals:\n"
            "• Immediately (now)\n"
            "• After 1 day\n"
            "• After 3 days\n"
            "• After 7 days\n"
            "• After 15 days\n"
            "• After 30 days",
            reply_markup=create_main_menu_keyboard()
        )

@app.on_callback_query(filters.regex(r"^subject_"))
async def handle_subject_selection(client, callback_query):
    """Handle subject selection"""
    subject = callback_query.data.split("_")[1]
    user = get_user(callback_query.from_user.id)
    
    if user.get("state") == "awaiting_topic_subject":
        update_user_state(
            user["user_id"],
            "awaiting_topic_notes",
            {
                "topic_name": user["state_data"]["topic_name"],
                "subject": subject
            }
        )
        
        await callback_query.message.edit_text(
            f"📚 Topic: *{user['state_data']['topic_name']}*\n"
            f"🔬 Subject: *{subject}*\n\n"
            "Would you like to add any notes? (Optional)\n"
            "Example: *Key points, page numbers, or reminders*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Skip Notes", callback_data="skip_notes")]
            ])
        )
    await callback_query.answer()

@app.on_callback_query(filters.regex(r"^skip_notes$"))
async def skip_notes(client, callback_query):
    """Handle skipping notes"""
    user = get_user(callback_query.from_user.id)
    topic_data = user.get("state_data", {})
    
    if user.get("state") == "awaiting_topic_notes":
        topic = create_topic(
            user["user_id"],
            topic_data["topic_name"],
            topic_data["subject"]
        )
        update_user_state(user["user_id"], None)
        
        await callback_query.message.edit_text(
            f"✅ *Topic added successfully!*\n\n"
            f"📖 *Topic:* {topic['topic_name']}\n"
            f"🔬 *Subject:* {topic['subject']}\n\n"
            "Your revisions are scheduled at these intervals:\n"
            "• Immediately (now)\n"
            "• After 1 day\n"
            "• After 3 days\n"
            "• After 7 days\n"
            "• After 15 days\n"
            "• After 30 days",
            reply_markup=create_main_menu_keyboard()
        )
    await callback_query.answer()

@app.on_callback_query(filters.regex(r"^cancel$"))
async def cancel_operation(client, callback_query):
    """Cancel current operation"""
    user_id = callback_query.from_user.id
    update_user_state(user_id, None)
    
    await callback_query.message.edit_text(
        "Operation cancelled.",
        reply_markup=create_main_menu_keyboard()
    )
    await callback_query.answer()

@app.on_message(filters.command("due") | filters.regex(r"^📝 Due Revisions$"))
async def show_due_revisions(client: Client, message: Message):
    """Show all due revisions"""
    user_id = message.from_user.id
    due_revisions = get_due_revisions(user_id)
    
    if not due_revisions:
        await message.reply_text(
            "🎉 You have no due revisions at the moment!\n\n"
            "Check back later or add new topics to study.",
            reply_markup=create_main_menu_keyboard()
        )
        return
    
    response = ["📝 *Due Revisions:*\n"]
    buttons = []
    
    for rev in due_revisions[:10]:  # Limit to 10 revisions per message
        topic = topics_col.find_one({"_id": rev["topic_id"]})
        if not topic:
            continue
            
        response.append(
            f"📌 *{topic['topic_name']}* ({topic['subject']})\n"
            f"⏳ Interval: {rev['interval']} day(s)\n"
            f"📅 Due since: {format_revision_date(rev['due_date'])}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"Mark as done: {topic['topic_name'][:15]}...",
                callback_data=f"complete_{rev['_id']}"
            )
        ])
    
    if len(due_revisions) > 10:
        response.append(f"\n...and {len(due_revisions) - 10} more revisions.")
    
    buttons.append([InlineKeyboardButton("Back to Menu", callback_data="main_menu")])
    
    await message.reply_text(
        "\n".join(response),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^complete_"))
async def handle_complete_revision(client, callback_query):
    """Handle revision completion via button"""
    revision_id = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    
    try:
        revision = revisions_col.find_one({
            "_id": revision_id,
            "user_id": user_id,
            "completed": False
        })
        
        if not revision:
            await callback_query.answer("Revision not found or already completed")
            return
            
        success = complete_revision(revision["_id"])
        
        if not success:
            await callback_query.answer("Failed to update revision")
            return
            
        topic = topics_col.find_one({"_id": revision["topic_id"]})
        remaining_revisions = revisions_col.count_documents({
            "topic_id": revision["topic_id"],
            "completed": False
        })
        
        if remaining_revisions == 0:
            reply_text = (
                f"🎉 *All revisions completed for {topic['topic_name']}!*\n\n"
                "You've successfully completed all scheduled revisions for this topic. "
                "The information should now be firmly in your long-term memory!"
            )
        else:
            next_rev = revisions_col.find_one({
                "topic_id": revision["topic_id"],
                "completed": False
            }, sort=[("interval", 1)])
            
            reply_text = (
                f"✅ *Revision marked as done!*\n\n"
                f"📖 *Topic:* {topic['topic_name']}\n"
                f"📅 *Next revision in:* {next_rev['interval']} days\n"
                f"⏰ *Due on:* {format_revision_date(next_rev['due_date'])}\n\n"
                "Keep up the good work!"
            )
        
        await callback_query.message.edit_text(
            reply_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="main_menu")]
            ])
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error marking revision done: {e}")
        await callback_query.answer("An error occurred. Please try again.")

@app.on_message(filters.command("list") | filters.regex(r"^📚 My Topics$"))
async def list_topics(client: Client, message: Message):
    """List all active topics"""
    user_id = message.from_user.id
    topics = get_user_topics(user_id)
    
    if not topics:
        await message.reply_text(
            "You haven't added any topics yet.\n\n"
            "Use the 'Add Topic' button to get started!",
            reply_markup=create_main_menu_keyboard()
        )
        return
    
    response = ["📚 *Your Topics:*\n"]
    buttons = []
    
    for topic in topics[:5]:  # Show first 5 topics with details
        revisions = get_topic_revisions(topic["_id"])
        completed = sum(1 for r in revisions if r["completed"])
        total = len(revisions)
        
        next_rev = next((r for r in revisions if not r["completed"]), None)
        
        status = ""
        if completed == total:
            status = "✅ Completed all revisions"
        elif next_rev:
            status = f"⏳ Next in {next_rev['interval']} days"
        
        response.append(
            f"📌 *{topic['topic_name']}* ({topic['subject']})\n"
            f"📝 Notes: {topic['notes'][:50] + '...' if topic['notes'] else 'None'}\n"
            f"📊 Progress: {completed}/{total} revisions\n"
            f"{status}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"View {topic['topic_name'][:15]}...",
                callback_data=f"view_topic_{topic['_id']}"
            )
        ])
    
    if len(topics) > 5:
        response.append(f"\n...and {len(topics) - 5} more topics.")
        buttons.append([
            InlineKeyboardButton("View All Topics", callback_data="view_all_topics")
        ])
    
    buttons.append([InlineKeyboardButton("Back to Menu", callback_data="main_menu")])
    
    await message.reply_text(
        "\n".join(response),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats") | filters.regex(r"^📊 Statistics$"))
async def show_stats(client: Client, message: Message):
    """Show user statistics"""
    user_id = message.from_user.id
    
    # Count topics
    total_topics = topics_col.count_documents({"user_id": user_id})
    active_topics = topics_col.count_documents({
        "user_id": user_id,
        "is_active": True
    })
    
    # Count revisions
    total_revisions = revisions_col.count_documents({"user_id": user_id})
    completed_revisions = revisions_col.count_documents({
        "user_id": user_id,
        "completed": True
    })
    
    # Calculate completion percentage
    completion_pct = (completed_revisions / total_revisions * 100) if total_revisions > 0 else 0
    
    # Get most studied subject
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$subject", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 1}
    ]
    most_studied = list(topics_col.aggregate(pipeline))
    top_subject = most_studied[0]["_id"] if most_studied else "None"
    
    stats_text = (
        "📊 *Your Study Statistics*\n\n"
        "📚 *Topics:*\n"
        f"• Total: {total_topics}\n"
        f"• Active: {active_topics}\n\n"
        "🔄 *Revisions:*\n"
        f"• Total: {total_revisions}\n"
        f"• Completed: {completed_revisions}\n"
        f"• Completion: {completion_pct:.1f}%\n\n"
        f"🔬 *Most Studied Subject:* {top_subject}\n\n"
        "Keep up the good work! Use the buttons below to manage your studies."
    )
    
    await message.reply_text(
        stats_text,
        reply_markup=create_main_menu_keyboard()
    )

async def send_daily_reminders():
    """Send daily reminders to users about due revisions"""
    while True:
        now = datetime.utcnow()
        logger.info("Checking for due revisions...")
        
        # Get all users who have notifications enabled
        users = users_col.find({
            "notification_time": {
                "$lte": now.strftime("%H:%M"),
                "$gt": (now - timedelta(minutes=30)).strftime("%H:%M")
            }
        })
        
        for user in users:
            try:
                due_revisions = get_due_revisions(user["user_id"])
                if not due_revisions:
                    continue
                    
                message = [
                    "⏰ *Revision Reminder*\n\n"
                    f"You have {len(due_revisions)} revision{'s' if len(due_revisions) > 1 else ''} due:\n"
                ]
                buttons = []
                
                for rev in due_revisions[:5]:  # Limit to 5 to avoid message flooding
                    topic = topics_col.find_one({"_id": rev["topic_id"]})
                    if topic:
                        message.append(
                            f"📌 *{topic['topic_name']}* ({topic['subject']})\n"
                            f"⏳ Interval: {rev['interval']} day(s)\n"
                        )
                        buttons.append([
                            InlineKeyboardButton(
                                f"Mark as done: {topic['topic_name'][:15]}...",
                                callback_data=f"complete_{rev['_id']}"
                            )
                        ])
                
                if len(due_revisions) > 5:
                    message.append(f"\n...and {len(due_revisions) - 5} more.")
                
                buttons.append([
                    InlineKeyboardButton("View All Due Revisions", callback_data="view_due")
                ])
                
                await app.send_message(
                    user["user_id"],
                    "\n".join(message),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                
            except Exception as e:
                logger.error(f"Error sending reminder to {user['user_id']}: {e}")
        
        # Sleep for 30 minutes before checking again
        await asyncio.sleep(1800)

# Start the bot
if __name__ == "__main__":
    logger.info("Starting Ebbinghaus Revision Bot...")
    
    # Create indexes for better performance
    try:
        topics_col.create_index([("user_id", 1)])
        revisions_col.create_index([("user_id", 1)])
        revisions_col.create_index([("topic_id", 1)])
        revisions_col.create_index([("due_date", 1)])
        revisions_col.create_index([("completed", 1)])
    except Exception as e:
        logger.error(f"Error creating indexes: {e}")
    
    # Start reminder loop in background
    loop = asyncio.get_event_loop()
    reminder_task = loop.create_task(send_daily_reminders())
    
    # Run the bot
    app.run()
