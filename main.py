import logging
import os
import tempfile
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from docx import Document
import pdfplumber
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Шрифт
FONT_PATH = "TT Travels Next Trial Bold.ttf"
FONT_SIZE = 72

# Инициализация OpenAI
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Категории
CATEGORIES = [
    "Видеоролик",
    "360-кампания",
    "Креативный сиддинг",
    "Ивент",
    "Другое"
]

# Словарь для хранения контекста пользователя
user_context = {}

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь .docx или .pdf файл с брифом.")

# Обработка документов
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

    user_id = update.effective_user.id
    user_context[user_id] = {"brief": text}

    # Кнопки выбора категории
    keyboard = [
        [InlineKeyboardButton(cat, callback_data=cat)] for cat in CATEGORIES
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери категорию для генерации идей:", reply_markup=reply_markup)

# Обработка выбора категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    category = query.data
    brief = user_context.get(user_id, {}).get("brief", "")

    if not brief:
        await query.edit_message_text("Бриф не найден. Пожалуйста, начни сначала.")
        return

    logger.info(f"Генерируем идеи для категории: {category}")

    ideas = await generate_ideas(brief, category)
    await query.edit_message_text(f"Вот идеи для категории *{category}*:\n\n{ideas}", parse_mode="Markdown")

# Генерация идей через GPT
async def generate_ideas(text, category):
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
        f"Категория: {category}\n\n"
        f"Бриф:\n{text}"
    )
    completion = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content

# Обработка простого текста
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Для продолжения диалога
    if user_id in user_context:
        user_context[user_id]["last_input"] = text

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Ты креативный ассистент, отвечай чётко и развёрнуто."},
            {"role": "user", "content": text}
        ]
    )
    await update.message.reply_text(response.choices[0].message.content)

# Вспомогательные функции
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# 🔥 Главный запуск бота
if __name__ == "__main__":
    import asyncio

    async def main():
        TOKEN = os.environ["BOT_TOKEN"]
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_category_selection))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Бот запускается...")

        await app.run_polling()

    asyncio.run(main())
