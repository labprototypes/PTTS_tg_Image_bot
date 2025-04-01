import logging
import os
import tempfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Логгер
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
user_states = {}
active = True

FONT_BOLD_PATH = "TT_Travels_Next_Trial_Bold.ttf"
FONT_NORMAL_PATH = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
LOGO_PATH = "logo.svg"

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = True
    await update.message.reply_text("Привет! Я готов к работе. Просто напиши или пришли бриф.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = False
    await update.message.reply_text("Бот остановлен. Чтобы запустить снова, воспользуйся /start")

# Обработка документов
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
    await update.message.reply_text(
        "Выберите тип креатива:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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

    pdf_path = generate_pdf(ideas)
    await context.bot.send_document(chat_id=user_id, document=open(pdf_path, "rb"))

    user_states[user_id] = {"stage": "chatting", "history": [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": ideas}
    ]}

# Чат-режим
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    if not active:
        return

    user_id = update.effective_user.id
    state = user_states.get(user_id)

    if not state:
        user_input = update.message.text
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": user_input}]
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GPT ошибка в общем режиме: {e}")
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

# Промпт
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

def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# PDF генерация
def generate_pdf(text):
    temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    doc = SimpleDocTemplate(temp_pdf.name, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=80, bottomMargin=40)

    pdfmetrics.registerFont(TTFont("TTTravels", FONT_BOLD_PATH))
    pdfmetrics.registerFont(TTFont("TTNorms", FONT_NORMAL_PATH))

    # Define styles
    title_style = ParagraphStyle(
        "Title",
        fontName="TTTravels",
        fontSize=18,
        leading=22
    )

    subtitle_style = ParagraphStyle(
        "Subtitle",
        fontName="TTTravels",
        fontSize=14,
        leading=18
    )

    normal_style = ParagraphStyle(
        "Normal",
        fontName="TTNorms",
        fontSize=12,
        leading=18
    )

    elements = []
    for paragraph in text.split("\n\n"):
        if paragraph.strip().startswith("1)") or paragraph.strip().startswith("2)") or paragraph.strip().startswith("3)"): 
            elements.append(Paragraph(paragraph.strip(), title_style))  # Title
        elif paragraph.strip().startswith("4)") or paragraph.strip().startswith("5)"):
            elements.append(Paragraph(paragraph.strip(), subtitle_style))  # Subtitle
        else:
            elements.append(Paragraph(paragraph.strip().replace("\n", "<br/>"), normal_style))  # Normal text
        elements.append(Spacer(1, 12))

    drawing = svg2rlg(LOGO_PATH)

    def add_logo(canvas: Canvas, doc):
        width, height = A4
        logo_width = width * 0.1
        logo_scale = logo_width / drawing.width
        canvas.saveState()
        renderPDF.draw(drawing, canvas, x=40, y=height - 60, showBoundary=False, scale=logo_scale)
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_logo, onLaterPages=add_logo)
    return temp_pdf.name

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
