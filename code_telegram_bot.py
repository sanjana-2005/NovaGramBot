import logging
import mysql.connector
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters
import google.generativeai as genai
import time
import httpx
import os
from PIL import Image
import pytesseract  # Add pytesseract for OCR (Text Extraction from Images)
from bs4 import BeautifulSoup
import requests

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# MySQL Configuration
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "manager",
    "database": "telegram_bot",
}

# Google Gemini API Configuration
GEMINI_API_KEY = "AIzaSyBhKR0D_g1tMZoU9GZ2qtAU3nMoGbhqQOY"  # Replace with your Gemini API Key
genai.configure(api_key=GEMINI_API_KEY)

# Telegram Bot Token
TELEGRAM_TOKEN = "7601755484:AAGUIquUsQEHI3SeS0oBwog36S6UeAI1bI8"  # Replace with your Telegram Bot token

# Tesseract Command Path (Ensure the correct path for Windows)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Connect to MySQL
def get_db_connection():
    try:
        logger.info("Connecting to the MySQL database.")
        return mysql.connector.connect(**MYSQL_CONFIG)
    except mysql.connector.Error as err:
        logger.error(f"Error connecting to MySQL: {err}")
        return None

# Register a new user
async def start(update: Update, context: Application):
    logger.info("Handling /start command.")
    user = update.message.from_user
    chat_id = update.message.chat_id
    first_name = user.first_name
    username = user.username

    db = get_db_connection()
    if not db:
        await update.message.reply_text("Database connection failed. Please try again later.")
        return

    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
    if cursor.fetchone():
        await update.message.reply_text("You're already registered!")
        logger.info(f"User {username} (chat_id: {chat_id}) already registered.")
    else:
        cursor.execute(
            "INSERT INTO users (first_name, username, chat_id) VALUES (%s, %s, %s)",
            (first_name, username, chat_id),
        )
        db.commit()
        logger.info(f"New user registered: {username} (chat_id: {chat_id}).")
        await update.message.reply_text(f"Welcome, {first_name}! Please share your phone number.")

        contact_button = KeyboardButton(text="Share Phone Number", request_contact=True)
        reply_markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True)
        await update.message.reply_text("Tap the button below to share your phone number:", reply_markup=reply_markup)

    cursor.close()
    db.close()

# Save phone number
async def save_phone_number(update: Update, context: Application):
    logger.info("Saving user's phone number.")
    if update.message.contact:
        chat_id = update.message.chat_id
        phone_number = update.message.contact.phone_number

        db = get_db_connection()
        if not db:
            await update.message.reply_text("Database connection failed. Please try again later.")
            return

        cursor = db.cursor()
        cursor.execute("UPDATE users SET phone_number = %s WHERE chat_id = %s", (phone_number, chat_id))
        db.commit()

        logger.info(f"Phone number {phone_number} saved for chat_id: {chat_id}.")
        await update.message.reply_text("Phone number saved successfully!")

        cursor.close()
        db.close()

# Gemini AI-powered chat
async def handle_message(update: Update, context: Application):
    logger.info("Handling user message.")
    user_input = update.message.text
    chat_id = update.message.chat_id

    try:
        # Get response from Gemini API
        model = genai.GenerativeModel("gemini-pro")
        response = model.generate_content(user_input)
        bot_response = response.text if response.text else "I couldn't generate a response. Please try again."

    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        bot_response = "I'm having trouble connecting to Gemini AI. Please try again later."

    # Save chat history in MySQL
    db = get_db_connection()
    if db:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO chat_history (chat_id, user_input, bot_response) VALUES (%s, %s, %s)",
            (chat_id, user_input, bot_response),
        )
        db.commit()
        cursor.close()
        db.close()

    await update.message.reply_text(bot_response)

# Web search function
async def web_search(update: Update, context: Application):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text("Please provide a query to search.")
        return

    # Perform the web search using requests and BeautifulSoup
    search_url = f"https://www.google.com/search?q={query}"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(search_url, headers=headers)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        search_results = soup.find_all('div', {'class': 'BVG0Nb'})  # Extract search result links
        top_links = []
        for result in search_results[:5]:  # Limit to top 5 results
            link = result.find('a')['href']
            title = result.get_text()
            top_links.append(f"{title} - {link}")

        # Return top search results to the user
        bot_response = "\n\n".join(top_links) if top_links else "No relevant results found."
    else:
        bot_response = "Sorry, I couldn't fetch the search results. Please try again later."

    await update.message.reply_text(bot_response)

# Handle image and perform OCR if text is present
async def handle_image(update: Update, context: Application):
    file_id = update.message.photo[-1].file_id  # Get the largest image size
    retries = 3
    for attempt in range(retries):
        try:
            # Get the file object from Telegram's API
            file = await context.bot.get_file(file_id)
            download_path = f"image_{update.message.chat_id}.jpg"
            # Use the correct method to download the image file
            await file.download_to_drive(download_path)
            logger.info(f"Image downloaded for chat_id: {update.message.chat_id} at {download_path}.")
            
            # Perform OCR (extract text) from the image
            extracted_text = extract_text_from_image(download_path)
            if extracted_text:
                bot_response = f"I found the following text in your image: \n{extracted_text}"
                await update.message.reply_text(bot_response)

                # Save the extracted text to chat_history
                db = get_db_connection()
                if db:
                    cursor = db.cursor()
                    cursor.execute(
                        "INSERT INTO chat_history (chat_id, user_input, bot_response) VALUES (%s, %s, %s)",
                        (update.message.chat_id, "Image message", extracted_text),
                    )
                    db.commit()
                    cursor.close()
                    db.close()

            else:
                bot_response = "No text found in the image."
                await update.message.reply_text(bot_response)
                
                # Save the "no text found" message to chat_history
                db = get_db_connection()
                if db:
                    cursor = db.cursor()
                    cursor.execute(
                        "INSERT INTO chat_history (chat_id, user_input, bot_response) VALUES (%s, %s, %s)",
                        (update.message.chat_id, "Image message", "No text found in the image."),
                    )
                    db.commit()
                    cursor.close()
                    db.close()

            # Save the image in the 'images' table
            db = get_db_connection()
            if db:
                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO images (chat_id, image_url, image_path) VALUES (%s, %s, %s)",
                    (update.message.chat_id, file.file_path, download_path),
                )
                db.commit()
                cursor.close()
                db.close()

            break
        except httpx.ReadTimeout as e:
            logger.error(f"Timeout error (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2)  # Wait 2 seconds before retrying
            else:
                await update.message.reply_text("Sorry, I took too long to process your image. Please try again later.")
                break
        except Exception as e:
            logger.error(f"Unexpected error while handling image: {e}")
            await update.message.reply_text("An error occurred while processing your image. Please try again later.")

# Function to extract text from an image using OCR
def extract_text_from_image(image_path):
    try:
        image = Image.open(image_path)
        # Optional: Apply preprocessing like converting to grayscale or adjusting contrast to improve OCR accuracy
        image = image.convert('L')  # Convert image to grayscale
        # Use Tesseract OCR to extract text
        text = pytesseract.image_to_string(image)
        return text.strip() if text.strip() else None
    except Exception as e:
        logger.error(f"Error during OCR: {e}")
        return None

# Main function to start the bot
def main():
    logger.info("Starting Telegram bot.")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register the handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, save_phone_number))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))  # Handling image uploads
    application.add_handler(CommandHandler("websearch", web_search))  # Register web search

    logger.info("Bot is running. Awaiting updates.")
    application.run_polling()

if __name__ == "__main__":
    main()
