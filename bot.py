import os
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from dotenv import load_dotenv
import asyncio

# Load environment variables
load_dotenv()

# MongoDB setup
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client["ebbinghaus_bot"]
studies_collection = db["studies"]
users_collection = db["users"]

# Telegram bot setup
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
bot_token = os.getenv("BOT_TOKEN")

app = Client("ebbinghaus_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# Spaced repetition intervals (in days)
SPACED_REPETITION_SCHEDULE = [1, 3, 7, 15, 30]

# Start command
@app.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    user = users_collection.find_one({"user_id": user_id})
    
    if not user:
        users_collection.insert_one({
            "user_id": user_id,
            "first_name": message.from_user.first_name,
            "username": message.from_user.username,
            "created_at": datetime.now()
        })
    
    welcome_msg = """📚 **Ebbinghaus Forgetting Curve Bot** 📚

I'll help you remember what you study by reminding you to review at optimal intervals (1-3-7-15-30 days).

**Commands:**
/add - Add what you studied today
/list - Show your current study items
/help - Show this help message

Based on Hermann Ebbinghaus's research, spaced repetition helps you retain information longer!"""
    
    await message.reply(welcome_msg)

# Add study item
@app.on_message(filters.command("add"))
async def add_study_item(client, message):
    if len(message.command) < 2:
        await message.reply("Please specify what you studied. Example:\n`/add Biology Chapter 3`")
        return
    
    study_item = " ".join(message.command[1:])
    user_id = message.from_user.id
    
    # Insert study item with initial review dates
    first_review = datetime.now() + timedelta(days=SPACED_REPETITION_SCHEDULE[0])
    
    studies_collection.insert_one({
        "user_id": user_id,
        "study_item": study_item,
        "created_at": datetime.now(),
        "next_review": first_review,
        "repetition_stage": 0,
        "completed": False
    })
    
    await message.reply(f"**✅ Added: *{study_item}*\n\nI'll remind you to review this in 1 day!**")

# List study items
@app.on_message(filters.command("list"))
async def list_study_items(client, message):
    user_id = message.from_user.id
    items = list(studies_collection.find({
        "user_id": user_id,
        "completed": False
    }).sort("next_review", 1))
    
    if not items:
        await message.reply("You don't have any active study items. Add one with /add!")
        return
    
    response = "📖 **Your Study Items** 📖\n\n"
    for item in items:
        days_left = (item["next_review"] - datetime.now()).days
        response += f"• **{item['study_item']}** (Review in {days_left} days)\n"
    
    await message.reply(response)

# Mark as completed
@app.on_callback_query(filters.regex("^complete_"))
async def mark_completed(client, callback_query):
    study_id = callback_query.data.split("_")[1]
    
    studies_collection.update_one(
        {"_id": study_id},
        {"$set": {"completed": True}}
    )
    
    await callback_query.answer("Marked as completed!")
    await callback_query.message.edit_text(f"✅ {callback_query.message.text}\n\nMarked as completed!")

# Check for reviews needed
async def check_reviews():
    while True:
        now = datetime.now()
        items_to_review = list(studies_collection.find({
            "next_review": {"$lte": now},
            "completed": False
        }))
        
        for item in items_to_review:
            user_id = item["user_id"]
            study_item = item["study_item"]
            stage = item["repetition_stage"]
            
            # Calculate next review date
            if stage + 1 < len(SPACED_REPETITION_SCHEDULE):
                next_stage = stage + 1
                next_review = now + timedelta(days=SPACED_REPETITION_SCHEDULE[next_stage])
            else:
                # After final stage, review every 30 days
                next_stage = stage
                next_review = now + timedelta(days=30)
            
            # Update in database
            studies_collection.update_one(
                {"_id": item["_id"]},
                {"$set": {
                    "next_review": next_review,
                    "repetition_stage": next_stage
                }}
            )
            
            # Send reminder
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Mark as Completed", callback_data=f"complete_{item['_id']}")]
            ])
            
            stage_names = {
                0: "1 day later",
                1: "3 days later",
                2: "1 week later",
                3: "2 weeks later",
                4: "1 month later"
            }
            
            stage_name = stage_names.get(stage, f"stage {stage}")
            
            reminder_text = f"⏰ **Time to review!** ⏰\n\n*{study_item}*\n\nLast reviewed: {stage_name}"
            
            try:
                await app.send_message(
                    user_id,
                    reminder_text,
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Failed to send reminder to {user_id}: {e}")
        
        await asyncio.sleep(60 * 60)  # Check every hour

# Run the bot
async def main():
    await app.start()
    print("Bot started!")
    asyncio.create_task(check_reviews())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
