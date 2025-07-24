import os
import asyncio
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient

# Load environment variables
load_dotenv()

# Spaced repetition intervals based on Ebbinghaus forgetting curve (in hours)
SPACED_REPETITION_INTERVALS = [
    1,      # First repetition after 1 hour
    9,      # Second repetition after 9 hours (total 10 hours)
    24,     # Third repetition after 1 day (total 1 day 10 hours)
    72,     # Fourth repetition after 3 days (total 4 days 10 hours)
    168,    # Fifth repetition after 1 week (total 11 days 10 hours)
    336,    # Sixth repetition after 2 weeks (total 25 days 10 hours)
    720,    # Seventh repetition after 1 month (total ~55 days)
]

# Initialize Pyrogram client
app = Client(
    "spaced_repetition_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

# Initialize MongoDB client
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = mongo_client["Revision-bot"]
users_collection = db["users"]
subjects_collection = db["subjects"]
revisions_collection = db["revisions"]

async def get_next_repetition_time(repetition_count):
    """Get the next repetition time based on the current repetition count."""
    if repetition_count >= len(SPACED_REPETITION_INTERVALS):
        # If we've gone through all intervals, use the last one (monthly)
        return datetime.now() + timedelta(hours=SPACED_REPETITION_INTERVALS[-1])
    return datetime.now() + timedelta(hours=SPACED_REPETITION_INTERVALS[repetition_count])

async def schedule_revision_reminder(user_id, subject_id, repetition_count):
    """Schedule the next revision reminder."""
    next_repetition_time = await get_next_repetition_time(repetition_count)
    
    await revisions_collection.update_one(
        {"user_id": user_id, "subject_id": subject_id},
        {"$set": {
            "next_repetition": next_repetition_time,
            "repetition_count": repetition_count + 1,
            "completed": False
        }},
        upsert=True
    )
    
    # Calculate delay in seconds until next reminder
    delay = (next_repetition_time - datetime.now()).total_seconds()
    await asyncio.sleep(delay)
    
    # Check if the revision was marked as completed before sending reminder
    revision = await revisions_collection.find_one({
        "user_id": user_id,
        "subject_id": subject_id,
        "completed": False
    })
    
    if revision:
        subject = await subjects_collection.find_one({"_id": subject_id})
        if subject:
            await app.send_message(
                user_id,
                f"📚 Time to revise: **{subject['name']}**\n\n"
                f"🔹 Repetition #{repetition_count + 1}\n"
                f"🔹 Category: {subject.get('category', 'General')}\n\n"
                f"Reply with /done_{subject_id} when you've completed this revision.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Mark as Done", callback_data=f"done_{subject_id}")]
                ])
            )

async def send_reminders():
    """Check for pending reminders and send them."""
    while True:
        now = datetime.now()
        pending_reminders = revisions_collection.find({
            "next_repetition": {"$lte": now},
            "completed": False
        })
        
        async for reminder in pending_reminders:
            subject = await subjects_collection.find_one({"_id": reminder["subject_id"]})
            if subject:
                await app.send_message(
                    reminder["user_id"],
                    f"📚 Time to revise: **{subject['name']}**\n\n"
                    f"🔹 Repetition #{reminder['repetition_count'] + 1}\n"
                    f"🔹 Category: {subject.get('category', 'General')}\n\n"
                    f"Reply with /done_{subject['_id']} when you've completed this revision.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Mark as Done", callback_data=f"done_{subject['_id']}")]
                    ])
                )
        
        await asyncio.sleep(60)  # Check every minute

@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    """Send a welcome message when the command /start or /help is issued."""
    user_id = message.from_user.id
    
    # Register user if not already registered
    await users_collection.update_one(
        {"_id": user_id},
        {"$set": {"username": message.from_user.username, "first_name": message.from_user.first_name}},
        upsert=True
    )
    
    await message.reply_text(
        "📖 **Spaced Repetition Bot**\n\n"
        "This bot helps you remember what you learn by reminding you to revise "
        "based on the Ebbinghaus forgetting curve.\n\n"
        "**Commands:**\n"
        "/add - Add a new subject to revise\n"
        "/list - List all your subjects\n"
        "/stats - View your revision statistics\n\n"
        "The bot will automatically remind you when it's time to revise each subject."
    )

@app.on_message(filters.command("add"))
async def add_subject(client, message):
    """Add a new subject to revise."""
    user_id = message.from_user.id
    subject_name = " ".join(message.command[1:])
    
    if not subject_name:
        await message.reply_text("Please provide a subject name. Example: /add Mathematics")
        return
    
    # Add subject to database
    result = await subjects_collection.insert_one({
        "user_id": user_id,
        "name": subject_name,
        "created_at": datetime.now(),
        "category": "General"  # Default category
    })
    
    # Schedule first revision
    await schedule_revision_reminder(user_id, result.inserted_id, 0)
    
    await message.reply_text(
        f"✅ Subject **{subject_name}** added successfully!\n\n"
        f"I'll remind you to revise it based on the spaced repetition schedule."
    )

@app.on_message(filters.command("list"))
async def list_subjects(client, message):
    """List all subjects for the user."""
    user_id = message.from_user.id
    subjects = subjects_collection.find({"user_id": user_id}).sort("name", 1)
    
    subject_list = []
    async for subject in subjects:
        # Get next revision time if available
        revision = await revisions_collection.find_one({
            "user_id": user_id,
            "subject_id": subject["_id"]
        })
        
        if revision:
            next_rev = revision.get("next_repetition", "Not scheduled")
            if isinstance(next_rev, datetime):
                next_rev = next_rev.strftime("%Y-%m-%d %H:%M")
            rep_count = revision.get("repetition_count", 0)
        else:
            next_rev = "Not scheduled"
            rep_count = 0
        
        subject_list.append(
            f"🔹 **{subject['name']}**\n"
            f"   - Category: {subject.get('category', 'General')}\n"
            f"   - Repetitions: {rep_count}\n"
            f"   - Next revision: {next_rev}\n"
            f"   - /done_{subject['_id']} /delete_{subject['_id']}\n"
        )
    
    if subject_list:
        await message.reply_text(
            "📚 **Your Subjects:**\n\n" + "\n".join(subject_list)
        )
    else:
        await message.reply_text("You haven't added any subjects yet. Use /add to get started.")

@app.on_message(filters.command("stats"))
async def show_stats(client, message):
    """Show revision statistics for the user."""
    user_id = message.from_user.id
    
    # Count total subjects
    total_subjects = await subjects_collection.count_documents({"user_id": user_id})
    
    # Count completed revisions
    completed_revisions = await revisions_collection.count_documents({
        "user_id": user_id,
        "completed": True
    })
    
    # Count pending revisions
    pending_revisions = await revisions_collection.count_documents({
        "user_id": user_id,
        "completed": False,
        "next_repetition": {"$lte": datetime.now()}
    })
    
    await message.reply_text(
        "📊 **Your Revision Statistics:**\n\n"
        f"🔹 Total subjects: {total_subjects}\n"
        f"🔹 Completed revisions: {completed_revisions}\n"
        f"🔹 Pending revisions: {pending_revisions}\n\n"
        "Keep up the good work! Consistency is key to effective learning."
    )

@app.on_message()
async def handle_done_command(client, message):
    """Handle the done command using regex pattern matching."""
    user_id = message.from_user.id
    text = message.text or ""
    
    # Check if message matches the done pattern
    match = re.match(r"^/done_([0-9a-fA-F]{24})$", text)
    if not match:
        return
    
    subject_id = match.group(1)
    
    # Update revision as completed
    revision = await revisions_collection.find_one({
        "user_id": user_id,
        "subject_id": subject_id
    })
    
    if not revision:
        await message.reply_text("Subject not found or revision not scheduled.")
        return
    
    await revisions_collection.update_one(
        {"_id": revision["_id"]},
        {"$set": {"completed": True}}
    )
    
    # Schedule next revision
    await schedule_revision_reminder(user_id, subject_id, revision["repetition_count"])
    
    subject = await subjects_collection.find_one({"_id": subject_id})
    if subject:
        await message.reply_text(
            f"✅ Great job revising **{subject['name']}**!\n\n"
            f"I'll remind you again at the next scheduled time."
        )

@app.on_callback_query(filters.regex(r"^done_([0-9a-fA-F]{24})$"))
async def mark_as_done_callback(client, callback_query):
    """Handle the 'Mark as Done' button click."""
    user_id = callback_query.from_user.id
    subject_id = callback_query.data.split("_")[1]
    
    # Update revision as completed
    revision = await revisions_collection.find_one({
        "user_id": user_id,
        "subject_id": subject_id
    })
    
    if not revision:
        await callback_query.answer("Subject not found or revision not scheduled.")
        return
    
    await revisions_collection.update_one(
        {"_id": revision["_id"]},
        {"$set": {"completed": True}}
    )
    
    # Schedule next revision
    await schedule_revision_reminder(user_id, subject_id, revision["repetition_count"])
    
    subject = await subjects_collection.find_one({"_id": subject_id})
    if subject:
        await callback_query.answer()
        await callback_query.message.edit_text(
            f"✅ Great job revising **{subject['name']}**!\n\n"
            f"I'll remind you again at the next scheduled time."
        )

async def main():
    """Start the bot and the reminder scheduler."""
    await app.start()
    print("Bot started!")
    
    # Start the reminder scheduler in the background
    asyncio.create_task(send_reminders())
    
    await asyncio.Event().wait()  # Run forever

if __name__ == "__main__":
    asyncio.run(main())
