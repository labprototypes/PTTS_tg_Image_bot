import os
import logging
import tempfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)
from openai import OpenAI
from docx import Document
import pdfplumber

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF

# Логгер
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
user_states = {}
active = True  # Флаг работы бота

# Пути к файлам
LOGO_PATH = "logo.svg"
FONT_PATH = "TT_Travels_Next_Trial_Bold.ttf"

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я готов к работе. Просто напиши или пришли бриф.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = False
    await update.message.reply_text("Бот остановлен. Чтобы запустить снова, воспользуйся /start")

# Получение документа
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    if not active:
        return

    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "waiting_category"}

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
        [InlineKeyboardButton("Свой запрос", callback_data="custom")],
    ]
    await update.message.reply_text("Выберите тип креатива:", reply_markup=InlineKeyboardMarkup(keyboard))

# Выбор категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    if not active:
        return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    user_states[user_id]["category"] = data

    if data == "custom":
        user_states[user_id]["stage"] = "awaiting_custom_prompt"
        await query.edit_message_text("Напиши, что ты хочешь получить от GPT по брифу.")
        return

    await query.edit_message_text("Принято, в работе…")

    prompt = build_prompt(user_states[user_id]["text"], data)

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        ideas = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"GPT ошибка: {e}")
        await context.bot.send_message(chat_id=user_id, text="Ошибка при генерации идей.")
        return

    # Создание PDF
    try:
        pdf_path = generate_pdf(ideas)
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(chat_id=user_id, document=f, filename="ideas.pdf")
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}")
        await context.bot.send_message(chat_id=user_id, text="Не удалось создать PDF.")

    # Возврат в режим диалога
    user_states[user_id]["history"] = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": ideas},
    ]
    user_states[user_id]["stage"] = "chatting"

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    if not active:
        return

    user_id = update.effective_user.id
    state = user_states.get(user_id)

    if not state:
        # Свободный режим общения
        user_input = update.message.text
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": user_input}]
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GPT ошибка: {e}")
            await update.message.reply_text("Ошибка при обращении к GPT.")
            return
        await update.message.reply_text(reply)
        return

    if state.get("stage") == "awaiting_custom_prompt":
        user_prompt = update.message.text
        full_prompt = f"{user_prompt}\n\nБриф:\n{state['text']}"
        state["history"] = [{"role": "user", "content": full_prompt}]
        state["stage"] = "chatting"
    else:
        state["history"].append({"role": "user", "content": update.message.text})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=state["history"]
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"GPT ошибка в диалоге: {e}")
        await update.message.reply_text("Ошибка при обращении к GPT.")
        return

    await update.message.reply_text(reply)
    state["history"].append({"role": "assistant", "content": reply})

# Генерация PDF с логотипом и шрифтом
def generate_pdf(text):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    doc = SimpleDocTemplate(temp_file.name, pagesize=A4)

    pdfmetrics.registerFont(TTFont("TTTravels", FONT_PATH))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Custom", fontName="TTTravels", fontSize=12, leading=16))

    content = []
    for line in text.split("\n\n"):
        content.append(Paragraph(line.replace("\n", "<br/>"), styles["Custom"]))
        content.append(Spacer(1, 0.5 * cm))

    # Вставка логотипа
    def add_logo(canvas, doc):
        drawing = svg2rlg(LOGO_PATH)
        width = doc.width * 0.1  # 10% ширины
        height = width * drawing.height / drawing.width
        renderPDF.draw(drawing, canvas, doc.leftMargin, A4[1] - height - 1*cm)

    doc.build(content, onFirstPage=add_logo, onLaterPages=add_logo)
    return temp_file.name

# Построение промпта
def build_prompt(text, category):
    extra = ""
    if category == "video":
        extra = "\nДобавь раскадровку: опиши минимум 6 кадров с описанием и звуком в кадре."
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
        f"Каждый пункт должен быть раскрыт подробно, на 2–4 абзаца.\n"
        f"Тип креатива: {category}\n"
        f"{extra}\n\n"
        f"Бриф:\n{text}"
    )

# Текст из файлов
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# Запуск
if __name__ == "__main__":
    TOKEN = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_category_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запускается...")
    app.run_polling()
