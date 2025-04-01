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

user_texts = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь .docx файл, а затем напиши /brief для генерации идей."
    )

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

async def generate_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = user_texts.get(chat_id)

    if not text:
        await update.message.reply_text("Сначала отправь .docx файл.")
        return

    await update.message.reply_text("Генерирую идеи...")

    try:
        ideas_text = await generate_ideas(text)
        await update.message.reply_text(ideas_text[:4000])
        if len(ideas_text) > 4000:
            await update.message.reply_text(ideas_text[4000:8000])
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await update.message.reply_text("Произошла ошибка при генерации идей.")

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

def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

# Запуск без updater
if __name__ == "__main__":
    TOKEN = os.environ["BOT_TOKEN"]

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", generate_brief))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Бот запускается...")
    app.run_polling()
