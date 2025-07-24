import os
from pyrogram import Client, filters

app = Client(
    "test_session",
    api_id=22012880,
    api_hash="5b0e07f5a96d48b704eb9850d274fe1d",
    bot_token="7992660943:AAHd6rIFuatXvt8d3K58BwPAaWO95auGKrY"
)

@app.on_message(filters.command("ping"))
async def ping(client, message):
    print("Ping command received!")
    await message.reply("🏓 Pong!")

print("Starting test bot...")
app.run()
