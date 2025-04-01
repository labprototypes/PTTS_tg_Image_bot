import logging
import os
import tempfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
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
from fpdf import FPDF
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from PIL import Image

# Настройки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
user_states = {}
active = True

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = True
    await update.message.reply_text("Привет! Пришли мне бриф (.docx или .pdf), либо просто задай вопрос.")

# Команда /stop
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active
    active = False
    await update.message.reply_text("Бот остановлен. Включить снова — /start")

# Обработка документа
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await update.message.reply_text("Поддерживаются только .docx и .pdf файлы.")
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

# Обработка выбора категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # Отправка текста + PDF
    await context.bot.send_message(chat_id=user_id, text=ideas)
    pdf_path = generate_pdf(ideas)
    await context.bot.send_document(chat_id=user_id, document=InputFile(pdf_path))

    user_states[user_id]["history"] = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": ideas},
    ]
    user_states[user_id]["stage"] = "chatting"

# Обработка диалога
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active:
        return

    user_id = update.effective_user.id
    state = user_states.get(user_id)

    user_input = update.message.text

    # Свободный режим
    if not state:
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": user_input}]
            )
            reply = response.choices[0].message.content.strip()
            await update.message.reply_text(reply)
        except Exception as e:
            logger.error(f"GPT ошибка: {e}")
            await update.message.reply_text("Ошибка при обращении к GPT.")
        return

    if state.get("stage") == "awaiting_custom_prompt":
        full_prompt = f"{user_input}\n\nБриф:\n{state['text']}"
        state["history"] = [{"role": "user", "content": full_prompt}]
        state["stage"] = "chatting"
    elif state.get("stage") == "chatting":
        state["history"].append({"role": "user", "content": user_input})
    else:
        return  # временно отключаем диалог при ожидании выбора категории

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=state["history"]
        )
        reply = response.choices[0].message.content.strip()
        await update.message.reply_text(reply)
        state["history"].append({"role": "assistant", "content": reply})
    except Exception as e:
        logger.error(f"GPT ошибка в диалоге: {e}")
        await update.message.reply_text("Ошибка при обращении к GPT.")

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

# PDF генерация
def generate_pdf(text):
    from fpdf import FPDF
    font_path = "TT_Travels_Next_Trial_Bold.ttf"
    logo_path = "logo.svg"

    # Конвертация SVG логотипа в PNG
    drawing = svg2rlg(logo_path)
    temp_png = os.path.join(tempfile.gettempdir(), "logo.png")
    renderPM.drawToFile(drawing, temp_png, fmt="PNG")

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("TTTravels", "", font_path, uni=True)
    pdf.set_font("TTTravels", size=14)

    pdf.image(temp_png, x=10, y=8, w=40)
    pdf.ln(30)

    for line in text.split("\n"):
        pdf.multi_cell(0, 10, line)

    output_path = os.path.join(tempfile.gettempdir(), "result.pdf")
    pdf.output(output_path)
    return output_path

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
