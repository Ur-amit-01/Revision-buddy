import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from config import *
from pyrogram import Client, filters, types
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
            "notification_time": "09:00"
        }
        users_col.insert_one(user)
    return user

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

# Bot commands
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: types.Message):
    """Welcome message and instructions"""
    user = get_user(message.from_user.id)
    
    welcome_text = (
        "📚 *Ebbinghaus Revision Bot*\n\n"
        "This bot helps you remember what you study using the *spaced repetition* technique "
        "based on the Ebbinghaus Forgetting Curve.\n\n"
        "🔹 *How it works:*\n"
        "1. Add topics you study with `/add`\n"
        "2. The bot will schedule revisions at optimal intervals (1-3-7-15-30 days)\n"
        "3. You'll get notifications when it's time to review\n"
        "4. Mark revisions as done with `/done`\n\n"
        "📊 *Commands:*\n"
        "/add - Add new study topic\n"
        "/list - Show your topics\n"
        "/due - Show due revisions\n"
        "/done - Complete a revision\n"
        "/stats - View your progress\n"
    )
    
    await message.reply_text(welcome_text, disable_web_page_preview=True)

@app.on_message(filters.command("add"))
async def add_topic(client: Client, message: types.Message):
    """Add a new study topic"""
    user_id = message.from_user.id
    args = message.text.split(maxsplit=3)
    
    if len(args) < 4:
        await message.reply_text(
            "Please provide topic name and subject.\n"
            "Example: `/add Biology Human Digestive System Notes about enzymes and processes`"
        )
        return
    
    subject = args[1].capitalize()
    topic_name = args[2]
    notes = args[3] if len(args) > 3 else ""
    
    if subject not in ["Biology", "Chemistry", "Physics", "Other"]:
        await message.reply_text(
            "Please use one of these subjects: Biology, Chemistry, Physics, Other"
        )
        return
    
    topic = create_topic(user_id, topic_name, subject, notes)
    
    reply_text = (
        f"✅ *Topic added successfully!*\n\n"
        f"📖 *Topic:* {topic_name}\n"
        f"🔬 *Subject:* {subject}\n\n"
        f"Your revisions are scheduled at these intervals:\n"
        f"- Immediately (now)\n"
        f"- After 1 day\n"
        f"- After 3 days\n"
        f"- After 7 days\n"
        f"- After 15 days\n"
        f"- After 30 days\n\n"
        f"Use /due to see when your next revision is."
    )
    
    await message.reply_text(reply_text)

@app.on_message(filters.command("due"))
async def show_due_revisions(client: Client, message: types.Message):
    """Show all due revisions"""
    user_id = message.from_user.id
    due_revisions = get_due_revisions(user_id)
    
    if not due_revisions:
        await message.reply_text("🎉 You have no due revisions at the moment!")
        return
    
    response = ["📝 *Due Revisions:*\n"]
    
    for rev in due_revisions:
        topic = topics_col.find_one({"_id": rev["topic_id"]})
        if not topic:
            continue
            
        response.append(
            f"🔹 *{topic['topic_name']}* ({topic['subject']})\n"
            f"Interval: {rev['interval']} day(s)\n"
            f"Due since: {format_revision_date(rev['due_date'])}\n"
            f"Mark as done with: `/done {str(rev['_id'])[:8]}`\n"
        )
    
    await message.reply_text("\n".join(response))

@app.on_message(filters.command("done"))
async def mark_revision_done(client: Client, message: types.Message):
    """Mark a revision as completed"""
    user_id = message.from_user.id
    args = message.text.split()
    
    if len(args) < 2:
        await message.reply_text(
            "Please specify revision ID to mark as done.\n"
            "Example: `/done 5f8d3a2b`\n\n"
            "Use `/due` to see your pending revisions."
        )
        return
    
    revision_id = args[1]
    
    try:
        # MongoDB uses ObjectId which is 24 chars, but we accept partial matches
        revision = revisions_col.find_one({
            "_id": {"$regex": f"^{revision_id}"},
            "user_id": user_id,
            "completed": False
        })
        
        if not revision:
            await message.reply_text("Revision not found or already completed.")
            return
            
        success = complete_revision(revision["_id"])
        
        if not success:
            await message.reply_text("Failed to update revision. Please try again.")
            return
            
        topic = topics_col.find_one({"_id": revision["topic_id"]})
        remaining_revisions = revisions_col.count_documents({
            "topic_id": revision["topic_id"],
            "completed": False
        })
        
        if remaining_revisions == 0:
            reply_text = (
                f"🎉 *All revisions completed for {topic['topic_name']}!*\n\n"
                f"You've successfully completed all scheduled revisions for this topic. "
                f"The information should now be firmly in your long-term memory!"
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
                f"Keep up the good work!"
            )
        
        await message.reply_text(reply_text, parse_mode="markdown")
        
    except Exception as e:
        logger.error(f"Error marking revision done: {e}")
        await message.reply_text("An error occurred. Please try again.")

@app.on_message(filters.command("list"))
async def list_topics(client: Client, message: types.Message):
    """List all active topics"""
    user_id = message.from_user.id
    topics = get_user_topics(user_id)
    
    if not topics:
        await message.reply_text("You haven't added any topics yet. Use `/add` to get started!")
        return
    
    response = ["📚 *Your Topics:*\n"]
    
    for topic in topics:
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
            f"🔹 *{topic['topic_name']}* ({topic['subject']})\n"
            f"📝 Notes: {topic['notes'][:50] + '...' if topic['notes'] else 'None'}\n"
            f"📊 Progress: {completed}/{total} revisions\n"
            f"{status}\n"
        )
    
    await message.reply_text("\n".join(response))

@app.on_message(filters.command("stats"))
async def show_stats(client: Client, message: types.Message):
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
        f"📊 *Your Study Statistics*\n\n"
        f"📚 *Topics:*\n"
        f"- Total: {total_topics}\n"
        f"- Active: {active_topics}\n\n"
        f"🔄 *Revisions:*\n"
        f"- Total: {total_revisions}\n"
        f"- Completed: {completed_revisions}\n"
        f"- Completion: {completion_pct:.1f}%\n\n"
        f"🔬 *Most Studied Subject:* {top_subject}\n\n"
        f"Keep up the good work! Use `/due` to check your pending revisions."
    )
    
    await message.reply_text(stats_text)

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
                    f"⏰ *Revision Reminder*\n\n"
                    f"You have {len(due_revisions)} revision{'s' if len(due_revisions) > 1 else ''} due:\n"
                ]
                
                for rev in due_revisions[:5]:  # Limit to 5 to avoid message flooding
                    topic = topics_col.find_one({"_id": rev["topic_id"]})
                    if topic:
                        message.append(
                            f"🔹 *{topic['topic_name']}* ({topic['subject']})\n"
                            f"Interval: {rev['interval']} day(s)\n"
                            f"Mark done: `/done {str(rev['_id'])[:8]}`\n"
                        )
                
                if len(due_revisions) > 5:
                    message.append(f"\n...and {len(due_revisions) - 5} more.")
                
                message.append("\nUse `/due` to see all pending revisions.")
                
                await app.send_message(
                    user["user_id"],
                    "\n".join(message)
                )
                
            except Exception as e:
                logger.error(f"Error sending reminder to {user['user_id']}: {e}")
        
        # Sleep for 30 minutes before checking again
        await asyncio.sleep(1800)

@app.on_message(filters.command("settings"))
async def user_settings(client: Client, message: types.Message):
    """Configure user settings"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if len(message.text.split()) > 1:
        # Update settings
        args = message.text.split()
        if len(args) >= 3 and args[1] == "time":
            try:
                # Validate time format
                datetime.strptime(args[2], "%H:%M")
                users_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"notification_time": args[2]}}
                )
                await message.reply_text(
                    f"⏰ Notification time updated to {args[2]} (UTC)"
                )
            except ValueError:
                await message.reply_text(
                    "Please use HH:MM format (24-hour). Example: `/settings time 09:00`"
                )
        else:
            await message.reply_text(
                "Available settings:\n"
                "`/settings time HH:MM` - Set daily reminder time (UTC)\n\n"
                "Example: `/settings time 09:00`"
            )
    else:
        # Show current settings
        settings_text = (
            f"⚙️ *Your Settings*\n\n"
            f"🕒 *Daily reminder time:* {user.get('notification_time', 'Not set')} (UTC)\n\n"
            f"To change settings, use:\n"
            f"`/settings time HH:MM`\n\n"
            f"Example: `/settings time 09:00`"
        )
        await message.reply_text(settings_text)

# Start the bot
if __name__ == "__main__":
    import asyncio
    
    logger.info("Starting Ebbinghaus Revision Bot...")
    
    # Create indexes for better performance
    topics_col.create_index([("user_id", 1)])
    revisions_col.create_index([("user_id", 1)])
    revisions_col.create_index([("topic_id", 1)])
    revisions_col.create_index([("due_date", 1)])
    revisions_col.create_index([("completed", 1)])
    
    # Start reminder loop in background
    loop = asyncio.get_event_loop()
    reminder_task = loop.create_task(send_daily_reminders())
    
    # Run the bot
    app.run()
