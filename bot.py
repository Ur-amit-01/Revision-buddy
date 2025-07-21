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
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
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

# ======================
# HELPER FUNCTIONS
# ======================

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

def format_revision_date(dt: datetime) -> str:
    """Format datetime for display"""
    return dt.strftime("%b %d, %Y %H:%M")

# ======================
# KEYBOARDS
# ======================

def create_main_keyboard():
    """Create main menu keyboard"""
    return ReplyKeyboardMarkup(
        [
            ["➕ Add New Topic"],
            ["📝 View Due Revisions", "📚 My Topics"],
            ["📊 My Stats", "⚙️ Settings"]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def create_subject_keyboard():
    """Create subject selection keyboard"""
    keyboard = []
    # Create two columns
    for i in range(0, len(SUBJECTS), 2):
        row = SUBJECTS[i:i+2]
        keyboard.append([InlineKeyboardButton(subject, callback_data=f"subject_{subject}") for subject in row])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def create_topic_actions_keyboard(topic_id):
    """Create actions for a specific topic"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Mark Revised", callback_data=f"complete_{topic_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{topic_id}")
        ],
        [InlineKeyboardButton("🔙 Back to Topics", callback_data="back_to_topics")]
    ])

# ======================
# COMMAND HANDLERS
# ======================

@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    """Welcome message and instructions"""
    user = get_user(message.from_user.id)
    
    welcome_text = """
🌟 <b>Ebbinghaus Revision Bot</b> 🌟

I'll help you remember what you study using <i>spaced repetition</i> based on the Ebbinghaus Forgetting Curve.

<b>How to use:</b>
1. Add topics you're studying
2. I'll remind you when to review them
3. Mark revisions as done when completed

Use the buttons below to get started!
"""
    
    await message.reply_text(
        welcome_text,
        reply_markup=create_main_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

@app.on_message(filters.regex(r"^➕ Add New Topic$"))
async def add_topic_start(client: Client, message: Message):
    """Start topic creation process"""
    user_id = message.from_user.id
    update_user_state(user_id, "awaiting_topic_name")
    
    await message.reply_text(
        "📝 <b>Let's add a new topic!</b>\n\n"
        "Please send me the name of the topic you want to study.\n"
        "<i>Example: Human Digestive System</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=enums.ParseMode.HTML
    )

@app.on_message(filters.regex(r"^(📝 View Due Revisions|📚 My Topics|📊 My Stats|⚙️ Settings)$"))
async def handle_main_buttons(client: Client, message: Message):
    """Handle main menu buttons"""
    command = message.text
    
    if command == "📝 View Due Revisions":
        await show_due_revisions(client, message)
    elif command == "📚 My Topics":
        await list_topics(client, message)
    elif command == "📊 My Stats":
        await show_stats(client, message)
    elif command == "⚙️ Settings":
        await show_settings(client, message)

@app.on_message(filters.text & filters.private & ~filters.command)
async def handle_text_input(client: Client, message: Message):
    """Handle text input based on user state"""
    user = get_user(message.from_user.id)
    text = message.text.strip()
    
    if user.get("state") == "awaiting_topic_name":
        if len(text) > 100:
            await message.reply_text("Topic name too long (max 100 characters)")
            return
            
        update_user_state(
            user["user_id"],
            "awaiting_topic_subject",
            {"topic_name": text}
        )
        await message.reply_text(
            f"📚 <b>Topic:</b> {text}\n\n"
            "Now select the subject:",
            reply_markup=create_subject_keyboard(),
            parse_mode=enums.ParseMode.HTML
        )
    elif user.get("state") == "awaiting_topic_notes":
        topic_data = user.get("state_data", {})
        topic = create_topic(
            user["user_id"],
            topic_data["topic_name"],
            topic_data["subject"],
            text
        )
        update_user_state(user["user_id"], None)
        
        await message.reply_text(
            f"🎉 <b>Topic added successfully!</b>\n\n"
            f"📖 <b>Topic:</b> {topic['topic_name']}\n"
            f"🔬 <b>Subject:</b> {topic['subject']}\n\n"
            "Your revisions are scheduled at optimal intervals to maximize retention!",
            reply_markup=create_main_keyboard(),
            parse_mode=enums.ParseMode.HTML
        )

# ======================
# CALLBACK HANDLERS
# ======================

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
            f"📚 <b>Topic:</b> {user['state_data']['topic_name']}\n"
            f"🔬 <b>Subject:</b> {subject}\n\n"
            "Would you like to add any notes? (Optional)\n"
            "<i>Example: Key points, page numbers, or reminders</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Skip Notes", callback_data="skip_notes")],
                [InlineKeyboardButton("Cancel", callback_data="cancel")]
            ]),
            parse_mode=enums.ParseMode.HTML
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
            f"🎉 <b>Topic added successfully!</b>\n\n"
            f"📖 <b>Topic:</b> {topic['topic_name']}\n"
            f"🔬 <b>Subject:</b> {topic['subject']}\n\n"
            "Your revisions are scheduled at optimal intervals to maximize retention!",
            parse_mode=enums.ParseMode.HTML
        )
        await callback_query.message.reply(
            "What would you like to do next?",
            reply_markup=create_main_keyboard()
        )
    await callback_query.answer()

@app.on_callback_query(filters.regex(r"^cancel$"))
async def cancel_operation(client, callback_query):
    """Cancel current operation"""
    user_id = callback_query.from_user.id
    update_user_state(user_id, None)
    
    await callback_query.message.edit_text(
        "Operation cancelled.",
        parse_mode=enums.ParseMode.HTML
    )
    await callback_query.message.reply(
        "What would you like to do next?",
        reply_markup=create_main_keyboard()
    )
    await callback_query.answer()

# ======================
# FEATURE HANDLERS
# ======================

async def show_due_revisions(client: Client, message: Message):
    """Show all due revisions"""
    user_id = message.from_user.id
    due_revisions = get_due_revisions(user_id)
    
    if not due_revisions:
        await message.reply_text(
            "🎉 <b>You have no due revisions at the moment!</b>\n\n"
            "Check back later or add new topics to study.",
            reply_markup=create_main_keyboard(),
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    response = ["📝 <b>Due Revisions:</b>\n"]
    buttons = []
    
    for rev in due_revisions[:5]:  # Limit to 5 revisions per message
        topic = topics_col.find_one({"_id": rev["topic_id"]})
        if not topic:
            continue
            
        response.append(
            f"\n📌 <b>{topic['topic_name']}</b> ({topic['subject']})\n"
            f"⏳ <i>Interval:</i> {rev['interval']} day(s)\n"
            f"📅 <i>Due since:</i> {format_revision_date(rev['due_date'])}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"✅ Mark as done: {topic['topic_name'][:15]}",
                callback_data=f"complete_{rev['_id']}"
            )
        ])
    
    if len(due_revisions) > 5:
        response.append(f"\n...and {len(due_revisions) - 5} more revisions.")
    
    buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
    
    await message.reply_text(
        "\n".join(response),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )

async def list_topics(client: Client, message: Message):
    """List all active topics"""
    user_id = message.from_user.id
    topics = get_user_topics(user_id)
    
    if not topics:
        await message.reply_text(
            "📚 <b>You haven't added any topics yet.</b>\n\n"
            "Use the 'Add New Topic' button to get started!",
            reply_markup=create_main_keyboard(),
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    response = ["📚 <b>Your Topics:</b>\n"]
    buttons = []
    
    for topic in topics[:5]:  # Show first 5 topics
        revisions = get_topic_revisions(topic["_id"])
        completed = sum(1 for r in revisions if r["completed"])
        total = len(revisions)
        
        response.append(
            f"\n📌 <b>{topic['topic_name']}</b> ({topic['subject']})\n"
            f"📊 <i>Progress:</i> {completed}/{total} revisions\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"View {topic['topic_name'][:15]}",
                callback_data=f"view_topic_{topic['_id']}"
            )
        ])
    
    if len(topics) > 5:
        response.append(f"\n...and {len(topics) - 5} more topics.")
    
    buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
    
    await message.reply_text(
        "\n".join(response),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )

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
    
    await message.reply_text(
        f"📊 <b>Your Study Statistics</b>\n\n"
        f"📚 <b>Topics:</b>\n"
        f"• Total: {total_topics}\n"
        f"• Active: {active_topics}\n\n"
        f"🔄 <b>Revisions:</b>\n"
        f"• Total: {total_revisions}\n"
        f"• Completed: {completed_revisions}\n"
        f"• Completion: {completion_pct:.1f}%\n\n"
        "Keep up the good work!",
        reply_markup=create_main_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

async def show_settings(client: Client, message: Message):
    """Show settings menu"""
    user = get_user(message.from_user.id)
    
    await message.reply_text(
        f"⚙️ <b>Settings</b>\n\n"
        f"🕒 <b>Daily reminder time:</b> {user.get('notification_time', '09:00')} (UTC)\n\n"
        "To change your notification time, send:\n"
        "<code>/settime HH:MM</code>\n\n"
        "<i>Example: /settime 09:00</i>",
        reply_markup=create_main_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

# ======================
# BOT STARTUP
# ======================

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
    
    # Run the bot
    app.run()
