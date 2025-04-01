import logging
import os
import tempfile
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from docx import Document
from openai import OpenAI

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь .docx файл и напиши /brief, чтобы я сгенерировал идеи по содержанию."
    )

# Временное хранилище текстов по chat_id
user_texts = {}

# Обработка .docx файла
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    file_name = file.file_name.lower()

    if not file_name.endswith(".docx"):
        await update.message.reply_text("Пожалуйста, отправь файл в формате .docx.")
        return

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        new_file = await context.bot.get_file(file.file_id)
        await new_file.download_to_drive(custom_path=tf.name)

        text = extract_text_from_docx(tf.name)
        user_texts[update.message.chat_id] = text

    await update.message.reply_text("Файл получен. Напиши /brief для генерации идей.")

# Команда /brief
async def generate_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = user_texts.get(chat_id)

    if not text:
        await update.message.reply_text("Сначала отправь .docx файл.")
        return

    await update.message.reply_text("Извлекаю текст из брифа...")
    await update.message.reply_text("Генерирую идеи через GPT...")

    try:
        ideas_text = await generate_ideas(text)
        await update.message.reply_text(f"Вот идеи:\n\n{ideas_text[:4000]}")
        if len(ideas_text) > 4000:
            await update.message.reply_text(ideas_text[4000:8000])  # Если слишком длинный
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await update.message.reply_text("Ошибка при генерации идей.")

# Генерация идей через GPT по заданному шаблону
async def generate_ideas(text):
    prompt = (
        f"Ты креативщик. Придумай 5 уникальных идей по следующему брифу. "
        f"Формат каждой идеи:\n\n"
        f"1) Название идеи\n"
        f"2) Вводная часть\n"
        f"3) Короткое описание идеи\n"
        f"4) Полное описание идеи\n"
        f"5) Реализация идеи\n"
        f"   5.1) Если это видеоролик – предложи сценарий\n"
        f"   5.2) Если это 360-кампания – предложи раскладку по каналам\n"
        f"   5.3) Если это креативный сиддинг – предложи механику\n"
        f"   5.4) Если это ивент – предложи реализацию\n\n"
        f"Бриф:\n{text}"
    )

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()

# Текст из .docx
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

# Фейковый HTTP-сервер (для Render)
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running.")

def run_fake_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# Запуск
if __name__ == "__main__":
    import asyncio

    async def run_bot():
        TOKEN = os.environ["BOT_TOKEN"]
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("brief", generate_brief))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

        logger.info("Бот запускается...")

        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

    Thread(target=run_fake_server).start()
    asyncio.run(run_bot())
