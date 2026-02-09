import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import google.generativeai as genai
from flask import Flask
from threading import Thread

# --- FLASK KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "I am alive!"
def run_http(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run_http); t.start()

# --- CONFIG ---
# Get these from your Render Environment Variables
API_ID = int(os.getenv("API_ID"))       # <--- NEW
API_HASH = os.getenv("API_HASH")        # <--- NEW
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") # Same as before
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# Initialize the Super Bot
app_bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- ANALYSIS LOGIC ---
async def analyze_pdf(client, message, file_path):
    try:
        msg = await message.reply_text("📥 Downloading big file... (This may take a minute)")
        
        # Pyrogram handles the download of large files automatically
        await client.download_media(message.document, file_name=file_path)
        
        await msg.edit_text("🤖 Reading newspaper with Gemini...")
        
        # Analyze with Gemini
        uploaded_file = genai.upload_file(path=file_path)
        prompt = """
        Analyze this newspaper.
        1. Top 3 Articles (Headline + 1 sentence summary).
        2. 5 Vocab words (Word: Meaning - Context).
        Format clearly with emojis.
        """
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([prompt, uploaded_file])
        
        # Create Buttons
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save Data", callback_data="save"), 
             InlineKeyboardButton("🗑️ Close", callback_data="close")]
        ])
        
        await msg.edit_text(response.text, reply_markup=buttons)

    except Exception as e:
        await message.reply_text(f"Error: {e}")
    
    finally:
        if os.path.exists(file_path): os.remove(file_path)

# --- HANDLERS ---
@app_bot.on_message(filters.document)
async def handle_document(client, message):
    # Check if it is a PDF
    if message.document.mime_type == "application/pdf":
        file_path = f"downloads/{message.document.file_id}.pdf"
        await analyze_pdf(client, message, file_path)
    else:
        await message.reply_text("Please send a PDF file.")

@app_bot.on_callback_query()
async def handle_callbacks(client, callback_query):
    if callback_query.data == "close":
        await callback_query.message.delete()
    elif callback_query.data == "save":
        await callback_query.answer("Feature coming in next update!", show_alert=True)

if __name__ == '__main__':
    keep_alive()
    print("Super Bot is running...")
    app_bot.run()
