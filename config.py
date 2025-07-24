import os
from dotenv import load_dotenv

    # Telegram API credentials
API_ID = int(os.getenv("API_ID", 22012880))
API_HASH = os.getenv("API_HASH", "5b0e07f5a96d48b704eb9850d274fe1d")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7992660943:AAHd6rIFuatXvt8d3K58BwPAaWO95auGKrY")
    
    # MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://uramit0001:EZ1u5bfKYZ52XeGT@cluster0.qnbzn.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = "ebbinghaus_bot"

    # Ebbinghaus revision intervals (in days)
REVISION_INTERVALS = [0, 1, 3, 7, 15, 30]
    
    # Notification settings
DEFAULT_NOTIFICATION_TIME = "09:00"  # UTC time
REMINDER_CHECK_INTERVAL = 1800  # 30 minutes in seconds
    
    # Bot behavior settings
MAX_REVISIONS_PER_REMINDER = 5  # To prevent message flooding
REVISION_ID_DISPLAY_LENGTH = 8  # How much of revision ID to show to users
    
    # Subject options
SUBJECTS = ["Biology", "Chemistry", "Physics", "Other"]
    
    # Timezone settings (all times stored in UTC)
TIMEZONE = "UTC"
    
    # Logging configuration
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

