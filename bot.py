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

# --- NEW: CURATION PROMPTS ---
EXAM_PROMPTS = {
    "banking": """
    You are a strict mentor for **Banking Exams (IBPS/SBI/RBI)**. 
    Your job is to filter the newspaper and list ONLY articles relevant to:
    1. Economy & Finance (RBI, GDP, Inflation, Budget)
    2. Banking Awareness (Mergers, New Products, MoUs)
    3. Appointments & Resignations (Key posts only)
    
    SKIP EVERYTHING ELSE (Politics, Crime, Local News).
    If an article is borderline, ask yourself: "Will this be asked in the General Awareness section?"
    """,
    "ssc": """
    You are a mentor for **SSC CGL/CHSL Exams**.
    Filter the newspaper for FACT-BASED news:
    1. Awards & Honours
    2. Sports (Winners, Venues)
    3. Science & Tech (New missiles, satellites)
    4. Books & Authors
    5. Government Schemes
    
    SKIP Opinion pieces and Editorials. Focus on hard facts.
    """,
    "upsc": """
    You are a Faculty for **UPSC Civil Services**.
    Curate a "Must-Read List" for aspirants based on the Syllabus:
    1. GS-2: Polity, Constitution, IR, Social Justice.
    2. GS-3: Economy, Environment, Science, Security.
    3. Ethics examples.
    
    Map every article to its specific GS Paper (e.g., [GS-2: Polity]).
    """,
    "cat": """
    You are a Verbal Ability mentor for **CAT/MBA**.
    Identify the 3 most complex Editorials/Articles that are good for:
    1. Reading Comprehension practice.
    2. Vocabulary building.
    3. Critical Reasoning.
    
    Ignore simple news. Look for dense, argumentative text.
    """,
    "regulatory": """
    You are a mentor for **RBI Grade B / SEBI**.
    Filter for "Phase 1 & 2" relevance:
    1. ESI (Economic & Social Issues): Reports, Indices, Census, Schemes.
    2. Finance: SEBI guidelines, RBI Circulars.
    3. Agriculture & Rural Dev (ARD): For NABARD.
    """
}

# --- REVISED OUTPUT FORMAT ---
COMMON_INSTRUCTIONS = """
    Output Format (Strictly follow this):
    
    🎯 **TODAY'S READING LIST**:
    (List the top 5-7 most important articles. No more.)
    
    1. 📌 **[Headline]**
       *Syllabus Tag:* [e.g., Economy / GS-2 / Sports]
       *Why Read:* [1-sentence reason why this is relevant for the exam]
    
    2. 📌 **[Headline]**
       *Syllabus Tag:* [Tag]
       *Why Read:* [Reason]
       
    (Continue for up to 7 articles...)

    |||
    
    🧠 **KEY VOCABULARY**:
    1. [Word]: [Definition]
    2. [Word]: [Definition]
    3. [Word]: [Definition]
    4. [Word]: [Definition]
    5. [Word]: [Definition]
"""

# --- CORE LOGIC ---
async def start_analysis(client, chat_id, exam_type, message_to_edit):
    try:
        user_data = USER_DATA_STORE.get(chat_id)
        if not user_data or 'file_id' not in user_data:
            await message_to_edit.edit_text("⚠️ Error: File not found. Please upload again.")
            return

        file_id = user_data['file_id']
        file_path = f"downloads/{file_id}.pdf"
        
        await message_to_edit.edit_text(f"🔍 Curating Reading List for **{exam_type.upper()}**... ⏳")
        
        await client.download_media(file_id, file_name=file_path)
        
        uploaded_file = genai.upload_file(path=file_path)
        
        full_prompt = EXAM_PROMPTS[exam_type] + COMMON_INSTRUCTIONS
        
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([full_prompt, uploaded_file])
        final_text = response.text
        
        # Save to Memory
        USER_DATA_STORE[chat_id]['analysis'] = final_text
        USER_DATA_STORE[chat_id]['exam'] = exam_type
        
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
        USER_DATA_STORE[chat_id] = {'file_id': message.document.file_id}
        
        # Exam Selection Buttons
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 Banking", callback_data="exam_banking"), InlineKeyboardButton("🏛️ UPSC", callback_data="exam_upsc")],
            [InlineKeyboardButton("🚆 SSC", callback_data="exam_ssc"), InlineKeyboardButton("📈 Regulatory", callback_data="exam_regulatory")],
            [InlineKeyboardButton("🎓 CAT/MBA", callback_data="exam_cat")]
        ])
        
        await message.reply_text("Select your Target Exam to curate the Reading List:", reply_markup=buttons)
    else:
        await message.reply_text("Please send a PDF file.")


@app_bot.on_callback_query()
async def handle_callbacks(client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    if data.startswith("exam_"):
        exam_type = data.split("_")[1]
        await start_analysis(client, chat_id, exam_type, callback_query.message)
    
    elif data == "close":
        await callback_query.message.delete()
        
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
                reading_list = parts[0].replace("🎯 TODAY'S READING LIST:", "").strip()
                vocab_part = parts[1].replace("🧠 KEY VOCABULARY:", "").strip()
            else:
                reading_list = full_text[:1000]
                vocab_part = "See summary"

            today_date = datetime.date.today().strftime("%Y-%m-%d")
            exam_tag = user_data.get('exam', 'General').upper()
            
            # Save: [Date, Exam, Reading List, Vocab]
            SHEET_CONNECTION.append_row([today_date, exam_tag, reading_list, vocab_part])
            
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
    print("Super Bot (Curation Edition) is running...")
    app_bot.run()
