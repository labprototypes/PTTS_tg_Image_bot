import logging
import os
import tempfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, CallbackQueryHandler, filters
)
from docx import Document
import pdfplumber
from openai import OpenAI
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
from reportlab.platypus.flowables import KeepTogether

# Настройки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
user_states = {}
active = True

# Fonts
FONT_BOLD = "TTTravelsBold"
FONT_REGULAR = "TTNormsMedium"
pdfmetrics.registerFont(TTFont(FONT_BOLD, "TT_Travels_Next_Trial_Bold.ttf"))
pdfmetrics.registerFont(TTFont(FONT_REGULAR, "TT_Norms_Pro_Trial_Expanded_Medium.ttf"))

# PDF генерация
def generate_pdf(text):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    doc = SimpleDocTemplate(tmp.name, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=100, bottomMargin=60)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Title", fontName=FONT_BOLD, fontSize=20, leading=24))
    styles.add(ParagraphStyle(name="Subhead", fontName=FONT_BOLD, fontSize=12, leading=16))
    styles.add(ParagraphStyle(name="Body", fontName=FONT_REGULAR, fontSize=11, leading=15))

    flowables = []

    for idea in text.split("\n\n"):
        parts = idea.strip().split("\n")
        if not parts or all(not p.strip() for p in parts):
            continue

        idea_group = []

        for part in parts:
            if part.strip().startswith("1)") or part.strip().startswith("Название идеи"):
                idea_group.append(Paragraph(part, styles["Title"]))
            elif part.strip().startswith(tuple(f"{i})" for i in range(2, 6))):
                idea_group.append(Spacer(1, 8))
                idea_group.append(Paragraph(part, styles["Subhead"]))
            else:
                idea_group.append(Paragraph(part, styles["Body"]))

            idea_group.append(Spacer(1, 6))

        flowables.append(KeepTogether(idea_group))
        flowables.append(PageBreak())

    def add_logo(canvas, doc):
        logo = svg2rlg("logo.svg")
        width, height = A4
        logo_width = width * 0.1
        scale = logo_width / logo.width
        logo.scale(scale, scale)
        renderPDF.draw(logo, canvas, 40, height - 80)

    doc.build(flowables, onFirstPage=add_logo, onLaterPages=add_logo)
    return tmp.name

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = True
    await update.message.reply_text("Привет! Я готов к работе. Просто напиши сообщение или пришли бриф.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = False
    await update.message.reply_text("Бот остановлен. Чтобы запустить снова, воспользуйся /start")

# Документы
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

# Категория
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
    user_states[user_id]["stage"] = "chatting"
    user_states[user_id]["history"] = [{"role": "user", "content": prompt}, {"role": "assistant", "content": ideas}]

# Диалог
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    if not active:
        return

    user_id = update.effective_user.id
    state = user_states.get(user_id)

    if not state or state.get("stage") != "chatting":
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": update.message.text}]
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GPT ошибка в чате: {e}")
            await update.message.reply_text("Ошибка при обращении к GPT.")
            return

        await update.message.reply_text(reply)
        return

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
        f"Ты креативщик в международном агентстве. Твоя задача — придумать 5 уникальных и разноплановых идей, которые полностью решают задачу из брифа. "
        f"Будь подробным и конкретным. Избегай шаблонов, используй сторителлинг, инсайты, нестандартные повороты.\n\n"
        f"Формат каждой идеи:\n"
        f"1) Название идеи\n"
        f"2) Вводная часть (зачем эта идея нужна)\n"
        f"3) Короткое описание идеи (одно предложение)\n"
        f"4) Полное описание идеи\n"
        f"5) Реализация идеи:\n"
        f"   5.1) Если это видеоролик — предложи сценарий\n"
        f"   5.2) Если это 360-кампания — предложи раскладку по каналам\n"
        f"   5.3) Если это сиддинг — предложи механику\n"
        f"   5.4) Если это ивент — предложи реализацию\n\n"
        f"Тип креатива: {category}\n"
        f"{extra}\n\n"
        f"Бриф:\n{text}"
    )

# Извлечение текста
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
