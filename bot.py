import os
import asyncio
import datetime
import re
from io import BytesIO
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from threading import Thread
from gtts import gTTS  # <--- NEW LIBRARY FOR AUDIO

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

# --- PROMPTS ---
EXAM_PROMPTS = {
    "banking": """
    You are an expert mentor for **Banking Exams (IBPS PO, SBI PO, RBI Assistant)**.
    Your goal is to extract current affairs specifically for the **General Awareness (Mains)** section.
    
    **FILTER CRITERIA:**
    1. **Banking & Finance:** New RBI guidelines, Repo Rates, Bank Mergers, Penalty on Banks, New Loan Products, UPI updates.
    2. **Economy:** GDP predictions (IMF/World Bank/Moody's), GST Collections, Inflation data.
    3. **Business/MoUs:** Agreements between India and other nations, Acquisitions, Loans sanctioned by ADB/World Bank.
    4. **Appointments/Resignations:** CEOs of Banks, Heads of International Orgs.
    
    **STRICTLY IGNORE:** Politics, Cinema, Crime, and Opinions. Focus on FACTS and NUMBERS.
    """,

    "ssc": """
    You are a mentor for **SSC CGL/CHSL & Railway Exams**.
    Your goal is to extract **Static GK** and **One-Liner Current Affairs**.
    
    **FILTER CRITERIA:**
    1. **Awards & Honours:** Nobel, Booker, Padma, Gallantry awards.
    2. **Sports:** Tournament winners, Venues of upcoming events (Olympics/World Cup), Record breakers.
    3. **Books & Authors:** New book releases by famous personalities.
    4. **Science & Defence:** DRDO/ISRO launches, New Missiles, Joint Military Exercises (e.g., Yudh Abhyas).
    5. **Firsts:** "India's first..." or "World's first..." news.
    
    **TONE:** Factual, direct, and concise. No detailed analysis needed.
    """,

    "upsc": """
    You are a Senior Faculty for **UPSC Civil Services (IAS/IPS)**.
    Your goal is to map news to the **Mains Syllabus** and identifying **Prelims Keywords**.
    
    **FILTER & MAP CRITERIA:**
    1. **GS-2 (Polity & IR):** Bills in Parliament, Supreme Court Judgments (cite Case Name), International Treaties, Social Justice schemes.
    2. **GS-3 (Economy/Env/Sci):** Agriculture (MSP/Crops), Environment (COP Summits, new species), Security (Cyber threats), Economy (Fiscal policy).
    3. **Ethics (GS-4):** Find examples of integrity, corruption, or governance issues for case studies.
    
    **OUTPUT REQUIREMENT:** Always mention the specific GS Paper (e.g., [GS-2: Polity]) next to the headline.
    """,

    "cat": """
    You are a Verbal Ability mentor for **CAT/XAT/GMAT**.
    Do NOT summarize the news. Instead, analyze the **EDITORIALS/OP-EDS** for Reading Comprehension skills.
    
    **TASK:**
    1. Identify the **Main Argument** of the author.
    2. Identify the **Tone** of the passage (e.g., Acerbic, Optimistic, Critical, Dogmatic).
    3. Highlight **complex words** and **idioms** used in the text.
    4. Ignore factual news; focus only on Opinion/Editorial pages.
    """,

    "regulatory": """
    You are a mentor for **Regulatory Bodies (RBI Grade B / SEBI / NABARD)**.
    Focus exclusively on **Phase 1 & Phase 2** relevant topics.
    
    **FILTER CRITERIA:**
    1. **ESI (Economic & Social Issues):** Govt Schemes (Pradhan Mantri...), Census/SECC data, Reports (NITI Aayog, WEF, ILO), Poverty/Employment stats.
    2. **Finance:** SEBI Regulations, RBI Circulars, Financial Market trends, IPO norms.
    3. **Agriculture (for NABARD):** MSP, Irrigation schemes, Agri-credit data.
    
    **STRICTLY IGNORE:** Political rows, Sports, and Entertainment. Focus on Reports, Indices, and Committees.
    """
}

COMMON_INSTRUCTIONS = """
    Output Format (Strictly follow this with separators):
    
    🎯 **TODAY'S READING LIST**:
    1. 📌 **[Headline]**
       *Why Read:* [Specific exam relevance, e.g., "Important for Phase 1 GA"]
    2. 📌 **[Headline]**
       *Why Read:* [Reason]
    (List top 5-7 articles)

    |||
    
    🏛️ **RELATED STATIC GK**:
    1. [Topic] -> [Concept]
    2. [Topic] -> [Concept]

    |||
    
    🧠 **KEY VOCABULARY**:
    1. [Word]: [Definition]
    2. [Word]: [Definition]
    3. [Word]: [Definition]
    4. [Word]: [Definition]
    5. [Word]: [Definition]
"""

# --- HELPERS ---
def smart_split(text, limit=4000):
    if len(text) <= limit: return [text]
    parts = []
    while len(text) > limit:
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1: split_at = limit
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
        uploaded_file = genai.upload_file(path=file_path)
        
        full_prompt = EXAM_PROMPTS.get(exam_type, "") + COMMON_INSTRUCTIONS
        
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([full_prompt, uploaded_file])
        final_text = response.text
        
        try: uploaded_file.delete()
        except: pass

        USER_DATA_STORE[chat_id]['analysis'] = final_text
        USER_DATA_STORE[chat_id]['exam'] = exam_type
        
        # Display Logic
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save", callback_data="save"), InlineKeyboardButton("📝 Quiz", callback_data="quiz")],
            [InlineKeyboardButton("🔊 Listen", callback_data="audio"), InlineKeyboardButton("🇮🇳 Hindi", callback_data="translate")],
            [InlineKeyboardButton("❌ Close", callback_data="close")]
        ])

        if len(final_text) > 4000:
            parts = smart_split(final_text)
            for i, part in enumerate(parts):
                if i == 0: await message_to_edit.edit_text(part)
                else: await client.send_message(chat_id, part)
            await client.send_message(chat_id, "Select Action:", reply_markup=buttons)
        else:
            await message_to_edit.edit_text(final_text, reply_markup=buttons)

    except Exception as e:
        await message_to_edit.edit_text(f"Error: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

# --- NEW FEATURES ---

async def generate_quiz(client, chat_id, message_to_edit):
    user_data = USER_DATA_STORE.get(chat_id)
    analysis_text = user_data.get('analysis')
    if not analysis_text: return await message_to_edit.reply_text("⚠️ No analysis found.")
    
    await message_to_edit.reply_text("🧠 Generating Quiz... ⏳")
    prompt = f"Based on this:\n{analysis_text}\nCreate 5 MCQs using Telegram Spoiler ||answer|| format."
    
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(prompt)
        await message_to_edit.reply_text(f"📝 **QUIZ**\n\n{response.text}", parse_mode=filters.enums.ParseMode.MARKDOWN)
    except Exception as e:
        await message_to_edit.reply_text(f"Error: {e}")

async def generate_audio(client, chat_id, message_to_edit):
    user_data = USER_DATA_STORE.get(chat_id)
    analysis_text = user_data.get('analysis')
    if not analysis_text: return await message_to_edit.reply_text("⚠️ No analysis found.")

    await message_to_edit.reply_text("🔊 Generating Audio Podcast... ⏳")
    
    # Clean text for better audio (remove complex formatting chars)
    clean_text = re.sub(r'[*_`]', '', analysis_text[:3000]) # Limit to 3000 chars for speed
    
    try:
        # Create MP3 in memory
        tts = gTTS(clean_text, lang='en')
        audio_file = BytesIO()
        tts.write_to_fp(audio_file)
        audio_file.name = "daily_news_podcast.mp3"
        
        await client.send_audio(chat_id, audio_file, title=f"Daily News - {datetime.date.today()}")
    except Exception as e:
        await message_to_edit.reply_text(f"Audio Error: {e}")

async def translate_text(client, chat_id, message_to_edit):
    user_data = USER_DATA_STORE.get(chat_id)
    analysis_text = user_data.get('analysis')
    if not analysis_text: return await message_to_edit.reply_text("⚠️ No analysis found.")

    await message_to_edit.reply_text("🇮🇳 Translating to Hindi... ⏳")
    
    prompt = f"Translate this entire study summary into Hindi (Devanagari script):\n{analysis_text}"
    
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(prompt)
        
        # Split logic again in case translation is long
        parts = smart_split(response.text)
        for part in parts:
            await client.send_message(chat_id, part)
            
    except Exception as e:
        await message_to_edit.reply_text(f"Translation Error: {e}")

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
    
    if data != "ignore": await callback_query.answer("Processing...") 

    if data.startswith("exam_"):
        exam_type = data.split("_")[1]
        await start_analysis(client, chat_id, exam_type, callback_query.message)
    elif data == "quiz": await generate_quiz(client, chat_id, callback_query.message)
    elif data == "audio": await generate_audio(client, chat_id, callback_query.message)
    elif data == "translate": await translate_text(client, chat_id, callback_query.message)
    elif data == "close": await callback_query.message.delete()
        
    elif data == "save":
        if not SHEET_CONNECTION: return await callback_query.answer("❌ Sheets Error", show_alert=True)
        user_data = USER_DATA_STORE.get(chat_id)
        full_text = user_data.get('analysis')
        if not full_text: return await callback_query.answer("⚠️ Session expired.", show_alert=True)

        try:
            if "|||" in full_text:
                parts = full_text.split("|||")
                reading_list = parts[0].replace("🎯 TODAY'S READING LIST:", "").strip()
                static_gk = parts[1].replace("🏛️ RELATED STATIC GK:", "").strip() if len(parts) > 1 else ""
                vocab_part = parts[2].replace("🧠 KEY VOCABULARY:", "").strip() if len(parts) > 2 else ""
            else:
                reading_list = full_text[:500]; static_gk = ""; vocab_part = ""

            today_date = datetime.date.today().strftime("%Y-%m-%d")
            exam_tag = user_data.get('exam', 'General').upper()
            SHEET_CONNECTION.append_row([today_date, exam_tag, reading_list, static_gk, vocab_part])
            await callback_query.answer("✅ Saved!", show_alert=True)
            
            # Update buttons to show "Saved" state
            new_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Saved!", callback_data="ignore"), InlineKeyboardButton("📝 Quiz", callback_data="quiz")],
                [InlineKeyboardButton("🔊 Listen", callback_data="audio"), InlineKeyboardButton("🇮🇳 Hindi", callback_data="translate")],
                [InlineKeyboardButton("❌ Close", callback_data="close")]
            ])
            
            try:
                # Try to edit the button
                await callback_query.edit_message_reply_markup(reply_markup=new_buttons)
            except MessageNotModified:
                # If it's already edited, just ignore it silently
                pass
            except Exception as e:
                # If it's a real error, tell me
                print(f"Error updating buttons: {e}")

        except Exception as e:
            await callback_query.answer(f"Error saving: {e}", show_alert=True)
    
    elif data == "ignore": await callback_query.answer("Already saved! 💾")

if __name__ == '__main__':
    keep_alive()
    print("Super Bot (God Mode) is running...")
    app_bot.run()
