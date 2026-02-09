import os
import asyncio
import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery # <--- Fixed Import
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from threading import Thread

# --- FLASK KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "I am alive!"
def run_http(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run_http); t.start()

# --- CONFIG ---
# Ensure these match your Render Environment Variables exactly
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
except:
    print("⚠️ Error: Missing Environment Variables in Render")

# --- GOOGLE SHEETS SETUP ---
SHEET_CONNECTION = None
try:
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client_gs = gspread.authorize(creds)
    # CHANGE 'Daily News Tracker' TO YOUR EXACT SHEET NAME IF DIFFERENT
    SHEET_CONNECTION = client_gs.open("Daily News Tracker").sheet1
    print("✅ Connected to Google Sheets!")
except Exception as e:
    print(f"⚠️ Google Sheets Error: {e}")

genai.configure(api_key=GEMINI_API_KEY)
app_bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- ANALYSIS LOGIC ---
async def analyze_pdf(client, message, file_path):
    try:
        msg = await message.reply_text("📥 Downloading big file... (This relies on your internet speed)")
        
        # Pyrogram handles the download of large files automatically
        await client.download_media(message.document, file_name=file_path)
        
        await msg.edit_text("🤖 Reading newspaper with Gemini...")
        
        # Analyze with Gemini
        uploaded_file = genai.upload_file(path=file_path)
        
        prompt = """
        Analyze this newspaper for a competitive exam student.
        
        Output Format (Strictly follow this):
        TOP 3 ARTICLES:
        1. [Headline] - [1 sentence summary]
        2. [Headline] - [1 sentence summary]
        3. [Headline] - [1 sentence summary]
        
        |||
        
        VOCABULARY:
        1. [Word]: [Definition] ([Context])
        2. [Word]: [Definition] ([Context])
        3. [Word]: [Definition] ([Context])
        4. [Word]: [Definition] ([Context])
        5. [Word]: [Definition] ([Context])
        """
        
        # Using the stable model version
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([prompt, uploaded_file])
        
        # Create Save Button
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save to Google Sheet", callback_data="save"), 
             InlineKeyboardButton("❌ Close", callback_data="close")]
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
async def handle_callbacks(client, callback_query: CallbackQuery):
    if callback_query.data == "close":
        await callback_query.message.delete()
        
    elif callback_query.data == "save":
        if not SHEET_CONNECTION:
            await callback_query.answer("❌ Error: credentials.json not found.", show_alert=True)
            return

        try:
            # 1. Get the text
            full_text = callback_query.message.text.markdown
            
            # 2. Parse Data
            if "|||" in full_text:
                parts = full_text.split("|||")
                summary_part = parts[0].replace("TOP 3 ARTICLES:", "").strip()
                vocab_part = parts[1].replace("VOCABULARY:", "").strip()
            else:
                summary_part = full_text[:500]
                vocab_part = "See summary"

            # 3. Save to Sheet
            today_date = datetime.date.today().strftime("%Y-%m-%d")
            SHEET_CONNECTION.append_row([today_date, summary_part, vocab_part])
            
            # 4. Show Success Alert
            await callback_query.answer("✅ Saved successfully!", show_alert=True)
            
            # 5. Update Message Button
            new_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Saved!", callback_data="ignore"), 
                 InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            await callback_query.edit_message_reply_markup(reply_markup=new_buttons)
            
        except Exception as e:
            if "MESSAGE_NOT_MODIFIED" in str(e):
                pass
            else:
                await callback_query.answer(f"Error saving: {e}", show_alert=True)
    
    elif callback_query.data == "ignore":
        await callback_query.answer("Already saved! 💾")

if __name__ == '__main__':
    keep_alive()
    print("Super Bot with Sheets is running...")
    app_bot.run()
