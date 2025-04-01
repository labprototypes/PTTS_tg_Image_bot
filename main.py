import logging
import os
import tempfile

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from docx import Document
import pdfplumber
from openai import OpenAI

# Логгирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# GPT клиент
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Хранилище контекста
user_context = {}

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я помогу тебе с креативными идеями.\n\n"
        "Просто отправь мне .docx или .pdf с брифом, и я всё сделаю сам 💡"
    )

# обработка документа
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    file_name = file.file_name.lower()

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        new_file = await context.bot.get_file(file.file_id)
        await new_file.download_to_drive(custom_path=tf.name)

        # Чтение текста
        if file_name.endswith(".docx"):
            text = extract_text_from_docx(tf.name)
        elif file_name.endswith(".pdf"):
            text = extract_text_from_pdf(tf.name)
        else:
            await update.message.reply_text("Пожалуйста, отправьте .docx или .pdf файл.")
            return

    logger.info("Файл получен. Показываем категории.")

    # Сохраняем текст во временном хранилище
    user_context[update.message.from_user.id] = {"brief": text}

    keyboard = [
        [
            InlineKeyboardButton("Видеоролик", callback_data="video"),
            InlineKeyboardButton("360-кампания", callback_data="360"),
        ],
        [
            InlineKeyboardButton("Креативный сиддинг", callback_data="seeding"),
            InlineKeyboardButton("Ивент", callback_data="event"),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери формат идеи:", reply_markup=reply_markup)

# обработка выбора категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in user_context or "brief" not in user_context[user_id]:
        await query.edit_message_text("Сначала отправьте бриф.")
        return

    category = query.data
    user_context[user_id]["category"] = category

    await query.edit_message_text("Генерирую идеи, подождите немного...")

    brief = user_context[user_id]["brief"]
    category = user_context[user_id]["category"]

    ideas = await generate_ideas(brief, category)

    await context.bot.send_message(chat_id=query.message.chat_id, text=ideas)

# генерация идей
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
        f"Тип идеи: {category}\n"
        f"Бриф:\n{text}"
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        return "Произошла ошибка при генерации идей 😔"

# текстовые сообщения — докрутка
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = update.message.text

    if user_id not in user_context:
        await update.message.reply_text("Сначала отправьте бриф в виде .docx или .pdf.")
        return

    messages = [
        {"role": "system", "content": "Ты креативщик. Помоги докрутить идею."},
        {"role": "user", "content": message}
    ]

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=messages
        )
        answer = completion.choices[0].message.content.strip()
        await update.message.reply_text(answer)
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await update.message.reply_text("Произошла ошибка при общении с GPT.")

# извлечение текста из docx
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

# извлечение текста из pdf
def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# запуск
if __name__ == "__main__":
    import asyncio

    async def run_bot():
        TOKEN = os.environ["BOT_TOKEN"]
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))  # ✅ старт
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_category_selection))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Бот запускается...")

        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

    asyncio.run(run_bot())
