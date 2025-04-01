import os
import logging
import tempfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, filters
)
from docx import Document
import pdfplumber
from openai import OpenAI
from fpdf import FPDF
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
user_states = {}
active = True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я готов к работе. Просто напиши или пришли бриф.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = False
    await update.message.reply_text("Бот остановлен. Воспользуйся /start чтобы запустить снова.")

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
            await update.message.reply_text("Пожалуйста, отправьте файл в формате .docx или .pdf.")
            return

    user_states[user_id]["text"] = text

    keyboard = [
        [InlineKeyboardButton("Видеоролик", callback_data="video"),
         InlineKeyboardButton("360-кампания", callback_data="360")],
        [InlineKeyboardButton("Креативный сиддинг", callback_data="seeding"),
         InlineKeyboardButton("Ивент", callback_data="event")],
        [InlineKeyboardButton("Свой запрос", callback_data="custom")]
    ]
    await update.message.reply_text("Выберите тип креатива:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    user_states[user_id]["category"] = data

    if data == "custom":
        user_states[user_id]["stage"] = "awaiting_custom_prompt"
        await query.edit_message_text("Напиши свой запрос по брифу.")
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
        await context.bot.send_message(chat_id=user_id, text="Ошибка при генерации ответа.")
        return

    pdf_path = generate_pdf(ideas)
    await context.bot.send_document(chat_id=user_id, document=open(pdf_path, "rb"))
    os.remove(pdf_path)

    user_states[user_id] = {"stage": "chatting", "history": [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": ideas}
    ]}

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

def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

def build_prompt(text, category):
    extra = ""
    if category == "video":
        extra = "\nДобавь раскадровку: опиши минимум 6 кадров с описанием и звуком."
    return (
        f"Ты креативщик. Придумай 5 уникальных идей по следующему брифу. "
        f"Формат каждой идеи:\n"
        f"1) Название\n2) Вводная\n3) Краткое описание\n4) Полное описание\n"
        f"5) Реализация\n   - Сценарий / Каналы / Механика / Ивент\n"
        f"Тип креатива: {category}\n{extra}\n\nБриф:\n{text}"
    )

def generate_pdf(content):
    temp_path = tempfile.mktemp(suffix=".pdf")
    c = canvas.Canvas(temp_path, pagesize=A4)

    pdfmetrics.registerFont(TTFont("TTTravels", "TT_Travels_Next_Trial_Bold.ttf"))
    c.setFont("TTTravels", 12)

    # Логотип SVG
    drawing = svg2rlg("logo.svg")
    renderPDF.draw(drawing, c, 30, 780)

    # Текст
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Frame

    styles = getSampleStyleSheet()
    frame = Frame(40, 40, 510, 700, showBoundary=0)
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.platypus import Paragraph

    doc = SimpleDocTemplate(temp_path, pagesize=A4)
    story = [Paragraph(p, styles["Normal"]) for p in content.split("\n") if p.strip()]
    doc.build(story)

    return temp_path

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
