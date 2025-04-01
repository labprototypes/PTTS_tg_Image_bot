import logging
import os
import tempfile

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from docx import Document
import pdfplumber
from openai import OpenAI

# --- Логгер ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- GPT ---
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# --- Категории ---
CATEGORY_OPTIONS = [
    ("🎬 Видеоролик", "video"),
    ("📢 360-кампания", "campaign"),
    ("👥 Креативный сиддинг", "seeding"),
    ("🎉 Ивент", "event"),
    ("❓ Другое", "other"),
]

# --- Генерация идей ---
def build_prompt(text, category):
    return (
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
        f"Категория: {category}\n\n"
        f"Бриф:\n{text}"
    )

# --- Извлечение текста ---
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# --- Обработка документа ---
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    file_name = file.file_name.lower()

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        new_file = await context.bot.get_file(file.file_id)
        await new_file.download_to_drive(custom_path=tf.name)

        if file_name.endswith(".docx"):
            text = extract_text_from_docx(tf.name)
        elif file_name.endswith(".pdf"):
            text = extract_text_from_pdf(tf.name)
        else:
            await update.message.reply_text("Пожалуйста, отправьте .docx или .pdf файл.")
            return

    context.user_data["brief_text"] = text
    context.user_data["history"] = []

    logger.info("Файл получен, просим выбрать категорию.")
    buttons = [
        [InlineKeyboardButton(text, callback_data=data)]
        for text, data in CATEGORY_OPTIONS
    ]
    await update.message.reply_text(
        "Выберите формат идеи:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# --- Обработка выбора категории ---
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data
    brief = context.user_data.get("brief_text")

    if not brief:
        await query.edit_message_text("Бриф не найден.")
        return

    prompt = build_prompt(brief, category)
    context.user_data["history"] = [{"role": "user", "content": prompt}]

    await query.edit_message_text("Генерирую идеи... ⏳")

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=context.user_data["history"]
        )
        content = response.choices[0].message.content
        context.user_data["history"].append({"role": "assistant", "content": content})

        await query.message.reply_text(content)
    except Exception as e:
        logger.error(f"GPT ошибка: {e}")
        await query.message.reply_text("Произошла ошибка при генерации идей.")

# --- Диалог ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "history" not in context.user_data:
        await update.message.reply_text("Сначала отправьте бриф в .docx или .pdf формате.")
        return

    user_input = update.message.text
    context.user_data["history"].append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=context.user_data["history"]
        )
        content = response.choices[0].message.content
        context.user_data["history"].append({"role": "assistant", "content": content})

        await update.message.reply_text(content)
    except Exception as e:
        logger.error(f"GPT ошибка в диалоге: {e}")
        await update.message.reply_text("Ошибка при обращении к GPT.")

# --- Старт ---
if __name__ == "__main__":
    import asyncio

    async def run_bot():
        TOKEN = os.environ["BOT_TOKEN"]
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_category_selection))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Бот запускается...")

        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

    asyncio.run(run_bot())
