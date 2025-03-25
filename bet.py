import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import qrcode
from io import BytesIO
from datetime import datetime
from pymongo import MongoClient

# Load environment variables from .env file
load_dotenv()

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
try:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = os.getenv("ADMIN_ID")
    MONGO_URI = os.getenv("MONGO_URI")

    if not BOT_TOKEN or not ADMIN_ID or not MONGO_URI:
        raise ValueError("BOT_TOKEN, ADMIN_ID, or MONGO_URI is not set in the .env file.")

    ADMIN_ID = int(ADMIN_ID)  # Convert ADMIN_ID to integer
except Exception as e:
    logger.error(f"Error loading environment variables: {e}")
    exit(1)

# MongoDB connection
client = MongoClient(MONGO_URI)
db = client.betting_bot
users_collection = db.users
pending_confirmations_collection = db.pending_confirmations
results_collection = db.results
winners_collection = db.winners
settings_collection = db.settings

# Initialize collections if they don't exist
if "users" not in db.list_collection_names():
    users_collection.insert_one({})  # Initialize with an empty document

if "pending_confirmations" not in db.list_collection_names():
    pending_confirmations_collection.insert_one({})

if "results" not in db.list_collection_names():
    results_collection.insert_one({"heads": 0, "tails": 0})

if "winners" not in db.list_collection_names():
    winners_collection.insert_one({"winners": []})

if "settings" not in db.list_collection_names():
    settings_collection.insert_one({"next_betting_time": None, "betting_open": False, "total_slots": 30, "available_slots": 30, "result_announcement_time": None})

# Helper functions
def generate_qr_code(user_id: int) -> BytesIO:
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f"Payment for user {user_id}")
    qr.make(fit=True)
    img = qr.make_image(fill="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = users_collection.find_one({"user_id": user_id})
    if not user_data:
        users_collection.insert_one({
            "user_id": user_id,
            "name": update.message.from_user.full_name,
            "bet": None,  # Initialize bet as None
            "status": None,  # Initialize status as None
            "payment_attempts": 0
        })
    await update.message.reply_text(
        "🎉 *Welcome to the Heads or Tails Betting Bot!* 🎉\n\n"
        "Here are the commands you can use:\n\n"
        "👉 /start - Start the bot and see this message.\n"
        "👉 /bet - Place your bet (Heads or Tails).\n"
        "👉 /status - Check your payment and bet status.\n"
        "👉 /results - View the latest results.\n"
        "👉 /nextbet - Check the next betting time.\n"
        "👉 /slots - Check available betting slots.\n\n"
        "Good luck! 🍀",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📜 <b>Game Rules and Commands</b> 📜\n\n"
        "<b>Heads or Tails Betting Bot Rules:</b>\n"
        "1. You can place bets on either Heads or Tails.\n"
        "2. Each user can only place one bet until the betting round is reset.\n"
        "3. After placing a bet, you must send a payment screenshot for approval.\n"
        "4. Only approved bets will fill the available slots.\n"
        "5. If your selected flip wins get 2x\n"
        "6. 50rs is limited to bet winners get 100₹ for all\n\n"
        "<b>Available Commands:</b>\n"
        "👉 /start - Start the bot and see the welcome message.\n"
        "👉 For Any Kind Of Queries Or Help @matrix_betting_assistance_bot\n"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

async def bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = settings_collection.find_one()
    if not settings["betting_open"]:
        await update.message.reply_text(
            "🚫 *Betting is currently closed.*\n\n"
            "👉 Use /nextbet to check the next betting time.\n"
            "👉 Wait for the next round",
            parse_mode="Markdown"
        )
        return

    if settings["available_slots"] <= 0:
        await update.message.reply_text(
            "🚫 *All slots are full!*\n\n"
            "Please wait for the next betting round. ⏳",
            parse_mode="Markdown"
        )
        return

    keyboard = [
        [InlineKeyboardButton("Heads 🪙", callback_data="heads")],
        [InlineKeyboardButton("Tails 🪙", callback_data="tails")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎲 *Place Your Bet!*\n\n"
        "Choose Heads or Tails:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_bet_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_data = users_collection.find_one({"user_id": user_id})
    
    if not user_data:
        await query.message.reply_text("Please start the bot with /start first.")
        await query.answer()
        return

    # Check if the user already has a bet placed
    if user_data.get("bet") is not None:
        await query.message.reply_text(
            "🚫 *You have already placed a bet!*\n\n"
            "You can only place one bet until the next reset.\n"
            "👉 Use /status to check your current bet status.",
            parse_mode="Markdown"
        )
        await query.answer()
        return

    choice = query.data
    if choice not in ["heads", "tails"]:
        await query.message.reply_text("Invalid bet choice. Please choose Heads or Tails.")
        await query.answer()
        return

    # Update the user's bet in the database
    users_collection.update_one({"user_id": user_id}, {"$set": {"bet": choice}})
    
    # Generate QR code for payment
    qr_code = generate_qr_code(user_id)

    # Send the payment image (payment.png) to the user
    payment_image_path = "payment.jpeg"  # Ensure this path is correct
    await query.message.reply_photo(
        photo=open(payment_image_path, 'rb'),  # Open the payment image in binary mode
        caption=f"📤 *Payment QR Code*\n\n"
                f"Scan the QR code to pay for your bet on *{choice.upper()}*.\n\n"
                f"👉 After payment, send a screenshot of the payment confirmation.",
        parse_mode="Markdown"
    )
    
    await query.answer()
    
    # No decrement of available slots here; it only happens upon approval.
    
async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = users_collection.find_one({"user_id": user_id})
    
    if not user_data:
        await update.message.reply_text("Please start the bot with /start first.")
        return

    # Check if the user has placed a bet
    if not user_data.get("bet"):
        await update.message.reply_text("Please place a bet using the /bet command before sending a payment screenshot.")
        return

    # Check if the user has already sent the maximum number of payment attempts
    if user_data["payment_attempts"] >= 3:
        await update.message.reply_text(
            "🚫 *Maximum Attempts Reached!*\n\n"
            "You have exceeded the maximum number of payment attempts (3).\n"
            "Please contact the admin for assistance 📞",
            parse_mode="Markdown"
        )
        return

    # Update the user's status to "waiting" when they send a payment screenshot
    users_collection.update_one({"user_id": user_id}, {"$set": {"status": "waiting"}})

    # Increment the payment attempts
    users_collection.update_one({"user_id": user_id}, {"$inc": {"payment_attempts": 1}})
    
    # Notify the user of the final attempt if it’s the last one
    if user_data["payment_attempts"] + 1 == 3:
        await update.message.reply_text(
            "⚠️ *Final Attempt!*\n\n"
            "This is your final attempt to send a payment screenshot.\n"
            "If this attempt fails, you will need to contact the admin. 📞",
            parse_mode="Markdown"
        )

    # Forward the payment screenshot to the admin
    await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.message.chat.id, message_id=update.message.message_id)

    message = (
        f"📤 *New Payment Screenshot Received*\n\n"
        f"👤 User ID: {user_id}\n"
        f"📛 Name: {user_data['name']}\n"
        f"🎲 Bet: {user_data['bet']}\n"
        f"📊 Status: waiting\n"  # Status is now set to "waiting"
        f"📝 Payment Attempts: {user_data['payment_attempts'] + 1}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=message, parse_mode="Markdown")

    await update.message.reply_text(
        "✅ *Payment Screenshot Sent!*\n\n"
        "Your payment screenshot has been forwarded to the admin for approval.\n"
        "You will be notified once it is reviewed. ⏳",
        parse_mode="Markdown"
    )

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to perform this action.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return

    user_id = int(context.args[0])
    user_data = users_collection.find_one({"user_id": user_id})
    if user_data:
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"status": "approved", "payment_attempts": 0}}
        )

        # Decrement available slots when a user is approved
        settings_collection.update_one({}, {"$inc": {"available_slots": -1}})

        # Log the updated user data
        updated_user = users_collection.find_one({"user_id": user_id})
        logger.info(f"Updated user data: {updated_user}")

        pending_confirmations_collection.delete_one({"user_id": user_id})
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 *Your Bet Has Been Approved!*\n\n"
                 "Thank you for participating. Good luck! 🍀",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ User {user_id} has been approved.")
    else:
        await update.message.reply_text("🚫 User not found in pending confirmations.")

async def disapprove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage:* `/disapprove <user_id>`\n\n"
            "Example: `/disapprove 123456789`",
            parse_mode="Markdown"
        )
        return

    user_id = int(context.args[0])
    user_data = users_collection.find_one({"user_id": user_id})
    if user_data:
        users_collection.update_one({"user_id": user_id}, {"$set": {"status": "disapproved", "payment_attempts": 0}})
        pending_confirmations_collection.delete_one({"user_id": user_id})
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ *Payment Disapproved!*\n\n"
                 "Your payment has been disapproved. Please contact the admin for further details. 📞",
            parse_mode="Markdown"
        )
        await update.message.reply_text(
            f"✅ *User {user_id} has been disapproved.*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🚫 *User not found in pending confirmations.*",
            parse_mode="Markdown"
        )

async def show_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to perform this action.")
        return

    all_users = list(users_collection.find({}))
    total_users = len(all_users)
    
    if not all_users:
        await update.message.reply_text("📭 No users have started the bot yet.")
    else:
        message = f"👥 *All Users ({total_users}):*\n\n"
        for user in all_users:
            if 'user_id' not in user:
                logger.warning(f"Skipping user document without 'user_id': {user}")
                continue

            message += (
                f"👤 User ID: {user['user_id']}\n"
                f"📛 Name: {user.get('name', 'N/A')}\n"
                f"🎲 Bet: {user.get('bet', 'N/A')}\n"
                f"📊 Status: {user.get('status', 'N/A')}\n\n"
            )
        await update.message.reply_text(message, parse_mode="Markdown")

async def view_pending_confirmations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to perform this action.")
        return

    # Query for users who are waiting for payment approval
    pending_users = list(users_collection.find({"status": "waiting"}))
    
    if not pending_users:
        await update.message.reply_text("📭 No pending confirmations.")
    else:
        message = "👥 *Pending Confirmations:*\n\n"
        for user in pending_users:
            if 'user_id' not in user:
                logger.warning(f"Skipping user document without 'user_id': {user}")
                continue

            message += (
                f"👤 User ID: {user['user_id']}\n"
                f"📛 Name: {user.get('name', 'N/A')}\n"
                f"🎲 Bet: {user.get('bet', 'N/A')}\n"
                f"📊 Status: {user.get('status', 'N/A')}\n\n"
            )
        await update.message.reply_text(message, parse_mode="Markdown")

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = users_collection.find_one({"user_id": user_id})
    
    if not user_data:
        await update.message.reply_text(
            "🚫 *Please start the bot with /start first.*",
            parse_mode="Markdown"
        )
        return

    # Check if the user has placed a bet
    if not user_data.get("bet"):
        await update.message.reply_text(
            "🚫 *No Bet Placed!*\n\n"
            "You have not placed any bet yet. Use the /bet command to place your bet.",
            parse_mode="Markdown"
        )
        return

    user_status = user_data["status"]
    user_bet = user_data["bet"]  # Get the user's bet choice
    if user_status == "approved":
        await update.message.reply_text(
            f"✅ *Payment Approved!*\n\n"
            f"Your payment has been approved. You chose *{user_bet.upper()}*.\n"
            "Thank you for participating! 🎉\n\n"
            "Good luck! 🍀",
            parse_mode="Markdown"
        )
    elif user_status == "disapproved":
        await update.message.reply_text(
            "❌ *Payment Disapproved!*\n\n"
            "Your payment has been disapproved. Please contact the admin for further details. 📞",
            parse_mode="Markdown"
        )
    elif user_status == "waiting":
        await update.message.reply_text(
            "⏳ *Payment Pending Approval*\n\n"
            f"You chose *{user_bet.upper()}*.\n"
            "Your payment screenshot is under review. Please wait for the admin to approve it. 🕒",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🚫 *Unknown Status!*\n\n"
            "Your status is unclear. Please contact the admin for assistance. 📞",
            parse_mode="Markdown"
        )

async def declare_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage:* `/declare <heads|tails>`\n\n"
            "Example: `/declare heads`",
            parse_mode="Markdown"
        )
        return

    result = context.args[0].lower()
    if result not in ["heads", "tails"]:
        await update.message.reply_text(
            "❌ *Invalid Result!*\n\n"
            "Please use `heads` or `tails`.",
            parse_mode="Markdown"
        )
        return

    results_collection.update_one({}, {"$inc": {result: 1}})
    await update.message.reply_text(
        f"✅ *Result Declared!*\n\n"
        f"The result is: *{result.upper()}* 🎉",
        parse_mode="Markdown"
    )

    # Notify all users about the result
    for user in users_collection.find():
        try:
            if 'user_id' not in user:
                logger.warning(f"Skipping user document without 'user_id': {user}")
                continue  # Skip this document

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=f"🎉 *Results Are Out!*\n\n"
                     f"The result is: *{result.upper()}* 🎉\n\n"
                     f"Thank you for participating! 🍀",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send result notification to user {user.get('user_id', 'unknown')}: {e}")

    # Identify winners
    winners = [user["user_id"] for user in users_collection.find({"bet": result, "status": "approved"}) if 'user_id' in user]
    winners_collection.update_one({}, {"$set": {"winners": winners}}, upsert=True)
    
    # Reset available slots after the results are declared
    settings_collection.update_one({}, {"$set": {"available_slots": settings_collection.find_one()["total_slots"]}})

async def view_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Fetch results data with error handling
        results_data = results_collection.find_one()
        if not results_data:
            logger.warning("Results data not found in database")
            results_data = {"heads": 0, "tails": 0}  # Default values
        
        # Fetch settings data with error handling
        settings_data = settings_collection.find_one()
        if not settings_data:
            logger.warning("Settings data not found in database")
            settings_data = {"result_announcement_time": "Not set"}  # Default values
        
        # Get announcement time with default value
        announcement_time = settings_data.get("result_announcement_time", "Not set")
        
        # Check if results have been declared
        if results_data.get("heads", 0) == 0 and results_data.get("tails", 0) == 0:
            # No results yet
            message_text = (
                "🎲 <b>Results Status</b>\n\n"
                "📢 The results are not declared yet.\n"
                f"🕒 <b>Result Announcement Time</b>: {announcement_time}\n\n"
                "Check back later! 🚀"
            )
            await update.message.reply_text(message_text, parse_mode="HTML")
        else:
            # Results have been declared
            message_text = (
                "🎲 <b>Results Declared!</b>\n\n"
                f"✅ <b>Heads</b>: {results_data.get('heads', 0)} wins\n"
                f"✅ <b>Tails</b>: {results_data.get('tails', 0)} wins\n\n"
                f"🕒 <b>Result Announcement Time</b>: {announcement_time}\n\n"
                "Thank you for participating! 🎉"
            )
            await update.message.reply_text(message_text, parse_mode="HTML")
            
    except Exception as e:
        # Log the error and send a friendly message to the user
        logger.error(f"Error in view_results: {e}")
        await update.message.reply_text(
            "❌ <b>Error</b>\n\n"
            "There was a problem retrieving the results. Please try again later or contact the admin.",
            parse_mode="HTML"
        )
        
        # Notify admin about the error
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ <b>Error in /results command</b>\n\n"
                     f"User: {update.effective_user.id} ({update.effective_user.full_name})\n"
                     f"Error: {str(e)}",
                parse_mode="HTML"
            )
        except Exception:
            pass  # Ignore errors in error reporting



async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to perform this action.")
        return

    # Reset only users who have placed bets or made payments
    users_collection.update_many({"bet": {"$ne": None}}, {"$set": {"bet": None, "status": None, "payment_attempts": 0}})
    pending_confirmations_collection.delete_many({})
    winners_collection.update_one({}, {"$set": {"winners": []}})
    results_collection.update_one({}, {"$set": {"heads": 0, "tails": 0}})
    
    # Reset slots, next betting time, and result announcement time
    settings_collection.update_one({}, {
        "$set": {
            "available_slots": settings_collection.find_one()["total_slots"],
            "next_betting_time": None,
            "result_announcement_time": None
        }
    })

    # Notify all users
    for user in users_collection.find():
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text="ℹ️ *The betting round has been reset!*\n\n"
                     "You can now place a new bet using the /bet command. Good luck! 🍀",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send reset notification to user {user.get('user_id', 'unknown')}: {e}")

    await update.message.reply_text(
        "✅ *Reset Complete!*\n\n"
        "All participants, results, next betting time, and result announcement time have been reset.\n"
        "Users have been notified.",
        parse_mode="Markdown"
    )

async def view_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to perform this action.")
        return

    participants = list(users_collection.find({"status": "approved", "bet": {"$ne": None}}))
    
    if not participants:
        await update.message.reply_text("📭 No approved participants yet.")
    else:
        message = "👥 *Approved Participants:*\n\n"
        for user in participants:
            if 'user_id' not in user:
                logger.warning(f"Skipping user document without 'user_id': {user}")
                continue

            message += (
                f"👤 User ID: {user['user_id']}\n"
                f"📛 Name: {user.get('name', 'N/A')}\n"
                f"🎲 Bet: {user.get('bet', 'N/A')}\n"
                f"📊 Status: {user.get('status', 'N/A')}\n\n"
            )
        await update.message.reply_text(message, parse_mode="Markdown")


async def next_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = settings_collection.find_one()
    if settings["next_betting_time"]:
        await update.message.reply_text(
            f"📅 *Next Betting Round*\n\n"
            f"The next betting round is scheduled for: *{settings['next_betting_time']}* 🕒\n\n"
            f"Get ready to place your bets! 🎉",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "📅 *No Betting Round Scheduled*\n\n"
            "No betting round has been scheduled yet. Please check back later. ⏳",
            parse_mode="Markdown"
        )

async def view_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = settings_collection.find_one()
    await update.message.reply_text(
        f"🎰 *Available Slots*\n\n"
        f"Slots available: *{settings['available_slots']}* out of *{settings['total_slots']}*\n\n"
        f"Hurry up and place your bet! 🎉",
        parse_mode="Markdown"
    )

async def schedule_betting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage:* `/schedule <datetime>`\n\n"
            "Example: `/schedule 2023-10-31 18:00`",
            parse_mode="Markdown"
        )
        return

    try:
        next_betting_time = " ".join(context.args)
        settings_collection.update_one({}, {"$set": {"next_betting_time": next_betting_time}})
        await update.message.reply_text(
            f"✅ *Betting Scheduled!*\n\n"
            f"The next betting round is scheduled for: *{next_betting_time}* 🕒",
            parse_mode="Markdown"
        )

        # Notify all users about the new schedule
        for user in users_collection.find():
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📅 *New Betting Round Scheduled!*\n\n"
                         f"The next betting round is scheduled for: *{next_betting_time}* 🕒\n\n"
                         f"Get ready to place your bets! 🎉",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send schedule notification to user {user.get('user_id', 'unknown')}: {e}")
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Invalid Datetime Format!*\n\n"
            f"Please provide a valid datetime. Error: {e}",
            parse_mode="Markdown"
        )

async def fix_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to perform this action.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /fixslots <number>")
        return

    try:
        total_slots = int(context.args[0])
        settings_collection.update_one({}, {"$set": {"total_slots": total_slots, "available_slots": total_slots}})
        await update.message.reply_text(f"Total slots set to: {total_slots}")
    except ValueError:
        await update.message.reply_text("Invalid number. Please provide a valid integer.")

async def view_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to perform this action.")
        return

    winners_data = winners_collection.find_one()
    if not winners_data or not winners_data["winners"]:
        await update.message.reply_text("No winners yet.")
    else:
        message = "Winners:\n"
        for user_id in winners_data["winners"]:
            user_data = users_collection.find_one({"user_id": user_id})
            if user_data and 'user_id' in user_data:
                message += f"User ID: {user_data['user_id']}\nName: {user_data.get('name', 'N/A')}\nBet: {user_data.get('bet', 'N/A')}\n\n"
        await update.message.reply_text(message)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to perform this action.")
        return

    # Reset only users who have placed bets or made payments
    users_collection.update_many({"bet": {"$ne": None}}, {"$set": {"bet": None, "status": None, "payment_attempts": 0}})
    pending_confirmations_collection.delete_many({})
    winners_collection.update_one({}, {"$set": {"winners": []}})
    results_collection.update_one({}, {"$set": {"heads": 0, "tails": 0}})

    # Reset slots, next betting time, and result announcement time
    settings_collection.update_one({}, {
        "$set": {
            "available_slots": settings_collection.find_one()["total_slots"],
            "next_betting_time": None,  # Reset next betting time
            "result_announcement_time": None  # Reset result announcement time
        }
    })

    # Notify all users
    for user in users_collection.find():
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text="ℹ️ *The betting round has been reset!*\n\n"
                     "You can now place a new bet using the /bet command. Good luck! 🍀",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send reset notification to user {user.get('user_id', 'unknown')}: {e}")

    await update.message.reply_text(
        "✅ *Reset Complete!*\n\n"
        "All participants, results, next betting time, and result announcement time have been reset.\n"
        "Users have been notified.",
        parse_mode="Markdown"
    )

async def admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "🔐 *Admin Commands*\n\n"
        "Here are the commands available to admins:\n\n"
        "👉 /approve <user_id> - Approve a user's payment.\n"
        "👉 /disapprove <user_id> - Disapprove a user's payment.\n"
        "👉 /pending - View pending payment confirmations.\n"
        "👉 /declare <heads|tails> - Declare the result of the betting round.\n"
        "👉 /participants - View all participants and their details.\n"
        "👉 /winners - View the winners of the latest round.\n"
        "👉 /reset - Reset all participants and data for the next round.\n"
        "👉 /schedule <datetime> - Schedule the next betting round.\n"
        "👉 /setannouncement <datetime> - Set the result announcement time.\n"
        "👉 /fixslots <number> - Set the total number of betting slots.\n"
        "👉 /showall - Show all users with count.\n"
        "👉 /open - Open betting for users.\n"
        "👉 /broadcast - Broadcast message.\n"
        "👉 /close - Close betting for users.\n\n"
        "Use these commands wisely! 🛠️",
        parse_mode="Markdown"
    )

async def set_announcement_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage:* `/setannouncement <datetime>`\n\n"
            "Example: `/setannouncement 2023-10-31 18:00`",
            parse_mode="Markdown"
        )
        return

    try:
        result_announcement_time = " ".join(context.args)
        settings_collection.update_one({}, {"$set": {"result_announcement_time": result_announcement_time}})
        await update.message.reply_text(
            f"✅ *Announcement Time Set!*\n\n"
            f"The results will be announced on: *{result_announcement_time}* 🕒",
            parse_mode="Markdown"
        )

        # Notify all users about the announcement time
        for user in users_collection.find():
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📢 *Result Announcement Scheduled!*\n\n"
                         f"The results will be announced on: *{result_announcement_time}* 🕒\n\n"
                         f"Stay tuned! 🚀",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send announcement notification to user {user['user_id']}: {e}")
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Invalid Datetime Format!*\n\n"
            f"Please provide a valid datetime. Error: {e}",
            parse_mode="Markdown"
        )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage:* `/broadcast <message>`\n\n"
            "Example: `/broadcast Hello everyone!`",
            parse_mode="Markdown"
        )
        return

    message = " ".join(context.args)
    await update.message.reply_text(
        f"✅ *Broadcasting Message!*\n\n"
        f"Your message is being sent to all users. 🚀",
        parse_mode="Markdown"
    )

    # Fetch all users from the database
    users = list(users_collection.find({}))
    total_users = len(users)
    
    # Counter for successful and failed sends
    success_count = 0
    fail_count = 0
    skipped_count = 0

    # Send the message to all users
    for user in users:
        if 'user_id' not in user:
            logger.warning(f"Skipping user document without 'user_id': {user}")
            skipped_count += 1
            continue
            
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=f"📢 *Broadcast Message*\n\n{message}",
                parse_mode="Markdown"
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast message to user {user.get('user_id', 'unknown')}: {e}")
            fail_count += 1

    # Send a detailed summary of the broadcast to the admin
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📊 *Broadcast Summary*\n\n"
             f"📋 Total users in database: *{total_users}*\n"
             f"✅ Successfully sent to: *{success_count}* users\n"
             f"❌ Failed to send to: *{fail_count}* users\n"
             f"⏭️ Skipped (invalid user_id): *{skipped_count}* users\n\n"
             f"Message: {message}",
        parse_mode="Markdown"
    )


async def close_betting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    settings_collection.update_one({}, {"$set": {"betting_open": False}})
    await update.message.reply_text(
        "✅ *Betting is Now Closed!*\n\n"
        "No further bets will be accepted. 🚫",
        parse_mode="Markdown"
    )

    # Notify all users
    for user in users_collection.find():
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text="🚫 *Betting is Now Closed!*\n\n"
                     "No further bets will be accepted. Please check the next betting time with /nextbet. ⏳",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send close betting notification to user {user['user_id']}: {e}")

async def open_betting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(
            "🚫 *You are not authorized to perform this action.*",
            parse_mode="Markdown"
        )
        return

    settings_collection.update_one({}, {"$set": {"betting_open": True}})
    await update.message.reply_text(
        "✅ *Betting is Now Open!*\n\n"
        "Users can now place their bets using the /bet command. 🎉"
        "Join Fast @Matrix_Bettings",
        parse_mode="Markdown"
    )

    # Notify all users
    for user in users_collection.find():
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text="🎉 *Betting is Now Open!*\n\n"
                     "Place your bets using the /bet command. Good luck! 🍀"
                     "Join Fast @Matrix_Bettings",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send open betting notification to user {user['user_id']}: {e}")

# Main function
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bet", bet))
    application.add_handler(CallbackQueryHandler(handle_bet_choice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_payment_screenshot))
    application.add_handler(CommandHandler("approve", approve_user))
    application.add_handler(CommandHandler("disapprove", disapprove_user))
    application.add_handler(CommandHandler("pending", view_pending_confirmations))
    application.add_handler(CommandHandler("declare", declare_result))
    application.add_handler(CommandHandler("results", view_results))
    application.add_handler(CommandHandler("participants", view_participants))
    application.add_handler(CommandHandler("status", check_status))
    application.add_handler(CommandHandler("nextbet", next_bet))
    application.add_handler(CommandHandler("slots", view_slots))
    application.add_handler(CommandHandler("schedule", schedule_betting))
    application.add_handler(CommandHandler("fixslots", fix_slots))
    application.add_handler(CommandHandler("winners", view_winners))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("close", close_betting))
    application.add_handler(CommandHandler("open", open_betting))
    application.add_handler(CommandHandler("setannouncement", set_announcement_time))
    application.add_handler(CommandHandler("adminview", admin_view)) 
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("showall", show_all_users))
    application.add_handler(CommandHandler("help", help_command))
    
    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()
