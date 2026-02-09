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

# --- PROMPTS (Kept same as your code) ---
EXAM_PROMPTS = {
    "banking": "You are a mentor for Banking Exams (IBPS/SBI). Focus on Economy, RBI, Finance.",
    "ssc": "You are a mentor for SSC CGL. Focus on Facts, Awards, Sports, Books.",
    "upsc": "You are a Faculty for UPSC. Focus on Policy, IR, Social Issues, Ethics.",
    "cat": "You are a Verbal Mentor for CAT. Focus on Editorial Tone & Arguments.",
    "regulatory": "You are a mentor for RBI Grade B. Focus on ESI & Finance."
}

COMMON_INSTRUCTIONS = """
    Output Format (Strictly follow this with separators):
    
    🎯 **TODAY'S READING LIST**:
    1. 📌 **[Headline]**
       *Why Read:* [Reason]
    (List top 5)

    |||
    
    🏛️ **RELATED STATIC GK**:
    1. [Topic] -> [Concept]
    2. [Topic] -> [Concept]

    |||
    
    🧠 **KEY VOCABULARY**:
    1. [Word]: [Definition]
    2. [Word]: [Definition]
"""

# --- IMPROVED SPLIT LOGIC ---
# Splits by newlines to avoid breaking Markdown
def smart_split(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    while len(text) > limit:
        # Find the last newline before the limit
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:  # No newline found, hard split
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:]
    parts.append(text)
    return parts

# --- CORE LOGIC ---
async def start_analysis(client, chat_id, exam_type, message_to_edit):
    try:
        user_data = USER_DATA_STORE.get(chat_id)
        if not user_data or 'file_id' not in user_data:
            await message_to_edit.edit_text("⚠️ Error: File not found. Upload again.")
            return

        file_id = user_data['file_id']
        file_path = f"downloads/{file_id}.pdf"
        
        await message_to_edit.edit_text(f"🔍 Curating Reading List + Static GK for **{exam_type.upper()}**... ⏳")
        
        await client.download_media(file_id, file_name=file_path)
        
        # [FIX]: Capture the file object to delete it later
        uploaded_file = genai.upload_file(path=file_path)
        
        full_prompt = EXAM_PROMPTS.get(exam_type, "") + COMMON_INSTRUCTIONS
        
        # [FIX]: Use stable model
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content([full_prompt, uploaded_file])
        final_text = response.text
        
        # [FIX]: Delete file from Cloud
        try:
            uploaded_file.delete()
        except:
            pass # Ignore if deletion fails

        USER_DATA_STORE[chat_id]['analysis'] = final_text
        USER_DATA_STORE[chat_id]['exam'] = exam_type
        
        # --- SPLIT MESSAGE LOGIC ---
        if len(final_text) > 4000:
            parts = smart_split(final_text)
            for i, part in enumerate(parts):
                if i == 0:
                    await message_to_edit.edit_text(part)
                else:
                    await client.send_message(chat_id, part)
            
            # Send buttons separately
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("💾 Save to Sheet", callback_data="save"), 
                 InlineKeyboardButton("📝 Take Quiz", callback_data="quiz")],
                [InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            await client.send_message(chat_id, "Select Action:", reply_markup=buttons)

        else:
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("💾 Save to Sheet", callback_data="save"), 
                 InlineKeyboardButton("📝 Take Quiz", callback_data="quiz")],
                [InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            await message_to_edit.edit_text(final_text, reply_markup=buttons)

    except Exception as e:
        await message_to_edit.edit_text(f"Error: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

# --- QUIZ LOGIC ---
async def generate_quiz(client, chat_id, message_to_edit):
    user_data = USER_DATA_STORE.get(chat_id)
    analysis_text = user_data.get('analysis')
    
    if not analysis_text:
        await message_to_edit.reply_text("⚠️ No analysis found.")
        return

    await message_to_edit.reply_text("🧠 Generating Quiz... ⏳")
    
    quiz_prompt = f"""
    Based on this analysis:
    {analysis_text}
    
    Create 5 MCQs. Format (Use Telegram Spoiler || answer ||):
    1. [Question]
    A) [Option]
    B) [Option]
    C) [Option]
    D) [Option]
    ✅ Answer: ||[Correct Option]||
    """
    
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(quiz_prompt)
        await message_to_edit.reply_text(f"📝 **TODAY'S QUIZ**\n\n{response.text}", parse_mode=filters.enums.ParseMode.MARKDOWN)
    except Exception as e:
        await message_to_edit.reply_text(f"Error: {e}")

# --- HANDLERS ---
@app_bot.on_message(filters.document)
async def handle_document(client, message):
    if message.document.mime_type == "application/pdf":
        chat_id = message.chat.id
        USER_DATA_STORE[chat_id] = {'file_id': message.document.file_id}
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 Banking", callback_data="exam_banking"), InlineKeyboardButton("🏛️ UPSC", callback_data="exam_upsc")],
            [InlineKeyboardButton("🚆 SSC", callback_data="exam_ssc"), InlineKeyboardButton("📈 Regulatory", callback_data="exam_regulatory")],
            [InlineKeyboardButton("🎓 CAT/MBA", callback_data="exam_cat")]
        ])
        await message.reply_text("Select your Target Exam:", reply_markup=buttons)
    else:
        await message.reply_text("Please send a PDF file.")


@app_bot.on_callback_query()
async def handle_callbacks(client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    # [FIX]: Answer immediately to stop spinner
    if data != "ignore": 
        await callback_query.answer("Processing...") 

    if data.startswith("exam_"):
        exam_type = data.split("_")[1]
        await start_analysis(client, chat_id, exam_type, callback_query.message)
    
    elif data == "quiz":
        await generate_quiz(client, chat_id, callback_query.message)

    elif data == "close":
        await callback_query.message.delete()
        
    elif data == "save":
        if not SHEET_CONNECTION:
            await callback_query.answer("❌ Error: Sheets not connected.", show_alert=True)
            return

        user_data = USER_DATA_STORE.get(chat_id)
        full_text = user_data.get('analysis')
        
        if not full_text:
            await callback_query.answer("⚠️ Session expired.", show_alert=True)
            return

        try:
            # [FIX]: Safe Parsing
            if "|||" in full_text:
                parts = full_text.split("|||")
                reading_list = parts[0].replace("🎯 TODAY'S READING LIST:", "").strip()
                # Check if parts exist before accessing index
                static_gk = parts[1].replace("🏛️ RELATED STATIC GK:", "").strip() if len(parts) > 1 else ""
                vocab_part = parts[2].replace("🧠 KEY VOCABULARY:", "").strip() if len(parts) > 2 else ""
            else:
                reading_list = full_text[:500]
                static_gk = ""
                vocab_part = ""

            today_date = datetime.date.today().strftime("%Y-%m-%d")
            exam_tag = user_data.get('exam', 'General').upper()
            
            SHEET_CONNECTION.append_row([today_date, exam_tag, reading_list, static_gk, vocab_part])
            
            await callback_query.answer("✅ Saved!", show_alert=True)
            
            new_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Saved!", callback_data="ignore"), 
                 InlineKeyboardButton("📝 Take Quiz", callback_data="quiz"),
                 InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            await callback_query.edit_message_reply_markup(reply_markup=new_buttons)

        except Exception as e:
            await callback_query.answer(f"Error saving: {e}", show_alert=True)
    
    elif data == "ignore":
        await callback_query.answer("Already saved! 💾")

if __name__ == '__main__':
    keep_alive()
    print("Super Bot (Final Fixed) is running...")
    app_bot.run()
