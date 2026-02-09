import os
import asyncio
import datetime
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified
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

# --- MAGIC SETUP ---
google_creds_env = os.getenv("GOOGLE_CREDENTIALS")
if google_creds_env:
    with open("credentials.json", "w") as f:
        f.write(google_creds_env)

# --- CONFIG ---
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
except:
    print("⚠️ Error: Missing Environment Variables")

# --- MEMORY STORE ---
# Structure: { chat_id: { 'file_id': '...', 'analysis': '...', 'exam': '...' } }
USER_DATA_STORE = {} 

# --- GOOGLE SHEETS SETUP ---
SHEET_CONNECTION = None
try:
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client_gs = gspread.authorize(creds)
    SHEET_CONNECTION = client_gs.open("Daily News Tracker").sheet1
    print("✅ Connected to Google Sheets!")
except Exception as e:
    print(f"⚠️ Google Sheets Error: {e}")

genai.configure(api_key=GEMINI_API_KEY)
app_bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- PROMPTS ---
EXAM_PROMPTS = {
    "banking": """
    Analyze this newspaper specifically for **Banking Exams (IBPS/SBI/RBI)**.
    Focus ONLY on: Economy, Finance, RBI Circulars, MoUs, Summits, Appointments, Mergers, and Banking Terminology.
    Ignore: Political drama, local crimes, entertainment.
    """,
    "ssc": """
    Analyze this newspaper specifically for **SSC CGL/CHSL Exams**.
    Focus ONLY on: Factual Current Affairs, Awards, Honours, Sports, Books & Authors, Science & Tech, and Places in News.
    Ignore: Deep editorial opinions, complex policy analysis.
    """,
    "upsc": """
    Analyze this newspaper specifically for **UPSC Civil Services**.
    Focus ONLY on: Govt Schemes, International Relations, Constitution/Polity, Environment, Science, and Social Issues.
    Provide a gist of the Editorial opinions.
    """,
    "cat": """
    Analyze this newspaper specifically for **CAT/MBA Exams**.
    Focus on: The Editorial Section.
    1. Summarize the main argument of the top editorial.
    2. Identify the Author's Tone (e.g., Critical, Sarcastic, Optimistic).
    3. List sophisticated vocabulary words used in the passage.
    """,
    "regulatory": """
    Analyze this newspaper specifically for **Regulatory Bodies (RBI Grade B / SEBI / NABARD)**.
    Focus ONLY on: ESI (Economic & Social Issues), Finance news, Agriculture (for NABARD), and Government Reports/Indices.
    """
}

COMMON_INSTRUCTIONS = """
    Output Format (Strictly follow this):
    TOP 3 UPDATES:
    1. [Headline] - [Summary suitable for this exam]
    2. [Headline] - [Summary suitable for this exam]
    3. [Headline] - [Summary suitable for this exam]
    
    |||
    
    VOCABULARY:
    1. [Word]: [Definition] ([Context])
    2. [Word]: [Definition] ([Context])
    3. [Word]: [Definition] ([Context])
    4. [Word]: [Definition] ([Context])
    5. [Word]: [Definition] ([Context])
"""

# --- CORE LOGIC ---
async def start_analysis(client, chat_id, exam_type, message_to_edit):
    try:
        # 1. Retrieve file_id from memory
        user_data = USER_DATA_STORE.get(chat_id)
        if not user_data or 'file_id' not in user_data:
            await message_to_edit.edit_text("⚠️ Error: File not found. Please upload again.")
            return

        file_id = user_data['file_id']
        file_path = f"downloads/{file_id}.pdf"
        
        await message_to_edit.edit_text(f"📥 Downloading & Analyzing for **{exam_type.upper()}**... ⏳")
        
        # 2. Download File
        # We need to fetch the file object using the file_id
        await client.download_media(file_id, file_name=file_path)
        
        # 3. Analyze
        uploaded_file = genai.upload_file(path=file_path)
        
        # Combine specific exam instructions with the formatting rules
        full_prompt = EXAM_PROMPTS[exam_type] + COMMON_INSTRUCTIONS
        
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([full_prompt, uploaded_file])
        final_text = response.text
        
        # 4. Save to Memory
        USER_DATA_STORE[chat_id]['analysis'] = final_text
        USER_DATA_STORE[chat_id]['exam'] = exam_type
        
        # 5. Show Result
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save to Sheet", callback_data="save"), 
             InlineKeyboardButton("❌ Close", callback_data="close")]
        ])
        await message_to_edit.edit_text(final_text, reply_markup=buttons)

    except Exception as e:
        await message_to_edit.edit_text(f"Error: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)


# --- HANDLERS ---
@app_bot.on_message(filters.document)
async def handle_document(client, message):
    if message.document.mime_type == "application/pdf":
        chat_id = message.chat.id
        
        # 1. Save file_id to memory (Don't download yet)
        USER_DATA_STORE[chat_id] = {'file_id': message.document.file_id}
        
        # 2. Ask for Exam Preference
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 Banking", callback_data="exam_banking"), InlineKeyboardButton("🏛️ UPSC", callback_data="exam_upsc")],
            [InlineKeyboardButton("🚆 SSC", callback_data="exam_ssc"), InlineKeyboardButton("📈 Regulatory", callback_data="exam_regulatory")],
            [InlineKeyboardButton("🎓 CAT/MBA", callback_data="exam_cat")]
        ])
        
        await message.reply_text("Which exam are you preparing for?", reply_markup=buttons)
    else:
        await message.reply_text("Please send a PDF file.")


@app_bot.on_callback_query()
async def handle_callbacks(client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    # --- EXAM SELECTION ---
    if data.startswith("exam_"):
        exam_type = data.split("_")[1] # e.g., "banking"
        await start_analysis(client, chat_id, exam_type, callback_query.message)
    
    # --- CLOSE ---
    elif data == "close":
        await callback_query.message.delete()
        
    # --- SAVE TO SHEET ---
    elif data == "save":
        if not SHEET_CONNECTION:
            await callback_query.answer("❌ Error: Sheets not connected.", show_alert=True)
            return

        user_data = USER_DATA_STORE.get(chat_id)
        full_text = user_data.get('analysis') if user_data else None
        
        if not full_text:
            await callback_query.answer("⚠️ Session expired.", show_alert=True)
            return

        try:
            if "|||" in full_text:
                parts = full_text.split("|||")
                summary_part = parts[0].replace("TOP 3 UPDATES:", "").strip()
                vocab_part = parts[1].replace("VOCABULARY:", "").strip()
            else:
                summary_part = full_text[:1000]
                vocab_part = "See summary"

            today_date = datetime.date.today().strftime("%Y-%m-%d")
            exam_tag = user_data.get('exam', 'General').upper()
            
            # FORMAT: [Date, Exam Category, Summary, Vocab]
            SHEET_CONNECTION.append_row([today_date, exam_tag, summary_part, vocab_part])
            
            await callback_query.answer("✅ Saved successfully!", show_alert=True)
            
            new_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Saved!", callback_data="ignore"), 
                 InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            await callback_query.edit_message_reply_markup(reply_markup=new_buttons)
            
        except MessageNotModified:
            pass
        except Exception as e:
            await callback_query.answer(f"Error saving: {e}", show_alert=True)
    
    elif data == "ignore":
        await callback_query.answer("Already saved! 💾")

if __name__ == '__main__':
    keep_alive()
    print("Super Bot (Exam Edition) is running...")
    app_bot.run()
