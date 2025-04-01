import logging
import os
import tempfile
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
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

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Подключение к OpenAI
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Состояния пользователя
user_states = {}


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "waiting_file"}
    await update.message.reply_text("Привет! Пришли мне .docx или .pdf файл с брифом.")


# Обработка входящих документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "waiting_category"}

    file = update.message.document
    file_name = file.file_name.lower()

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        new_file = await context.bot.get_file(file.file_id)
        await new_file.download_to_drive(custom_path=tf.name)

        # Определяем формат
        if file_name.endswith(".docx"):
            text = extract_text_from_docx(tf.name)
        elif file_name.endswith(".pdf"):
            text = extract_text_from_pdf(tf.name)
        else:
            await update.message.reply_text("Пожалуйста, отправьте .docx или .pdf файл.")
            return

    user_states[user_id]["text"] = text

    keyboard = [
        [
            InlineKeyboardButton("Видеоролик", callback_data="video"),
            InlineKeyboardButton("360-кампания", callback_data="360"),
        ],
        [
            InlineKeyboardButton("Креативный сиддинг", callback_data="seeding"),
            InlineKeyboardButton("Ивент", callback_data="event"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите тип креатива:", reply_markup=reply_markup)


# Обработка выбора категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    category = query.data
    user_states[user_id]["category"] = category
    text = user_states[user_id]["text"]

    prompt = build_prompt(text, category)

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        ideas = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await query.edit_message_text("Произошла ошибка при генерации идей.")
        return

    await query.edit_message_text("Готово! Вот сгенерированные идеи:")
    await context.bot.send_message(chat_id=user_id, text=ideas)

    # Сохраняем для продолжения диалога
    user_states[user_id]["history"] = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": ideas},
    ]
    user_states[user_id]["stage"] = "chatting"


# Обработка сообщений от пользователя в режиме диалога
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_states.get(user_id)

    if not state or "history" not in state:
        await update.message.reply_text("Пришли мне сначала бриф в виде .docx или .pdf.")
        return

    user_input = update.message.text
    state["history"].append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=state["history"]
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT в диалоге: {e}")
        await update.message.reply_text("Ошибка при обращении к GPT.")
        return

    await update.message.reply_text(reply)
    state["history"].append({"role": "assistant", "content": reply})


# Генерация промпта
def build_prompt(text, category):
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
        f"Тип креатива: {category}\n"
        f"Бриф:\n{text}"
    )
    return prompt


# Извлечение текста из DOCX
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])


# Извлечение текста из PDF
def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text


# Запуск бота
if __name__ == "__main__":
    TOKEN = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_category_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запускается...")
    app.run_polling()
