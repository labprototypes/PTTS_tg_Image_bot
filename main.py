import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from fpdf import FPDF
from docx import Document
from openai import OpenAI
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import tempfile

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=OPENAI_KEY)

# Шрифты
FONT_BOLD = "TTTravelsBold"
FONT_REGULAR = "TTNormsMedium"

pdfmetrics.registerFont(TTFont(FONT_BOLD, "TT_Travels_Next_Trial_Bold.ttf"))
pdfmetrics.registerFont(TTFont(FONT_REGULAR, "TT_Norms_Pro_Trial_Expanded_Medium.ttf"))

dialog_mode = True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь мне файл брифа или задай вопрос.")

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs if para.text.strip()])

def ask_gpt_brief_analysis(text, category="идеи спецпроекта"):
    system_prompt = (
        "Ты — стратегический креативщик. На основе клиентского брифа, тебе нужно предложить подробные, продуманные и свежие идеи. "
        "Ответ структурируй по заголовкам. Расписывай каждый пункт подробно, избегай шаблонов и общих фраз."
    )
    user_prompt = f"На основе этого брифа предложи идеи по категории: {category}.\n\n{str(text)}"

    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7
    )
    return response.choices[0].message.content

def generate_pdf(text):
    file_path = tempfile.mktemp(suffix=".pdf")
    doc = SimpleDocTemplate(file_path, pagesize=A4)
    elements = []

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MyTitle", fontName=FONT_BOLD, fontSize=22, leading=28, spaceAfter=12))
    styles.add(ParagraphStyle(name="MySubtitle", fontName=FONT_BOLD, fontSize=14, leading=18, spaceAfter=8))
    styles.add(ParagraphStyle(name="MyText", fontName=FONT_REGULAR, fontSize=12, leading=16, spaceAfter=6))

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("###"):
            elements.append(Paragraph(stripped[3:].strip(), styles["MyTitle"]))
        elif stripped.startswith("##"):
            elements.append(Paragraph(stripped[2:].strip(), styles["MySubtitle"]))
        elif stripped.startswith("-") or stripped.startswith("*") or stripped[0].isdigit():
            elements.append(Paragraph(stripped, styles["MyText"]))
        else:
            elements.append(Paragraph(stripped, styles["MyText"]))
        elements.append(Spacer(1, 8))

    # Добавляем логотип
    def add_logo(canvas_obj, doc):
        width, height = A4
        logo_path = "logo.svg"
        if os.path.exists(logo_path):
            logo_width = width * 0.1
            canvas_obj.drawImage(logo_path, 40, height - 80, width=logo_width, preserveAspectRatio=True, mask='auto')

    doc.build(elements, onFirstPage=add_logo, onLaterPages=add_logo)
    return file_path

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global dialog_mode
    dialog_mode = False

    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    file_path = tempfile.mktemp(suffix=".docx")
    await file.download_to_drive(file_path)

    text = extract_text_from_docx(file_path)
    context.user_data["brief_text"] = text

    keyboard = [[InlineKeyboardButton("Идеи для спецпроекта", callback_data="идея")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Файл получен. Выберите категорию:", reply_markup=reply_markup)

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global dialog_mode
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(text="Генерирую идеи...")

    brief_text = context.user_data.get("brief_text", "")
    ideas = ask_gpt_brief_analysis(brief_text)
    pdf_path = generate_pdf(ideas)

    with open(pdf_path, "rb") as f:
        await context.bot.send_document(chat_id=query.message.chat.id, document=f, filename="ideas_output.pdf")

    dialog_mode = True
    await context.bot.send_message(chat_id=query.message.chat.id, text="Готово! Можем продолжать диалог.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global dialog_mode
    if not dialog_mode:
        await update.message.reply_text("Сейчас я работаю над брифом. Скоро вернусь в диалог.")
        return

    prompt = update.message.text
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    await update.message.reply_text(response.choices[0].message.content)

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.FILE_EXTENSION("docx"), handle_file))
    app.add_handler(CallbackQueryHandler(handle_category_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()
