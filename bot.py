import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
from flask import Flask
from threading import Thread

# --- FLASK KEEP-ALIVE SERVER ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run_http():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- BOT CONFIG ---
# WE USE os.getenv SO WE DON'T LEAK SECRETS ON GITHUB
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Received! Processing PDF for {user_name}... ⏳")

    file_id = update.message.document.file_id
    new_file = await context.bot.get_file(file_id)
    file_path = "daily_paper.pdf"
    await new_file.download_to_drive(file_path)

    try:
        uploaded_file = genai.upload_file(path=file_path)
        prompt = """
        Analyze this newspaper for a student. 
        1. List top 3 articles with 1-sentence summaries.
        2. 'Rule of 5' Vocabulary: 5 hard words with definitions and context sentences.
        Output as clean text with emojis.
        """
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content([prompt, uploaded_file])
        await context.bot.send_message(chat_id=update.effective_chat.id, text=response.text)

    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Error: {e}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

if __name__ == '__main__':
    # Start the fake server first
    keep_alive()
    
    # Start the bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    pdf_handler = MessageHandler(filters.Document.PDF, handle_pdf)
    application.add_handler(pdf_handler)
    print("Bot is polling...")
    application.run_polling()
