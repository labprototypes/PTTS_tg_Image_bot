import os
import io
import logging
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from openai import OpenAI
from dotenv import load_dotenv
import docx
import pdfplumber
from fpdf import FPDF
import cairosvg

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я могу:\n"
        "- Ответить на любой вопрос\n"
        "- Сгенерировать изображение (начни с 'Картинка: ...')\n"
        "- Принять бриф через /brief и выдать 5 идей в PDF"
    )

# === GPT или генерация картинки ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text.lower().startswith("картинка:"):
        prompt = text.split("картинка:", 1)[1].strip()
        await update.message.reply_text("🎨 Генерирую изображение...")
        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1
            )
            image_url = response.data[0].url
            await update.message.reply_photo(photo=image_url)
        except Exception as e:
            await update.message.reply_text(f"Ошибка генерации изображения:\n{e}")
    else:
        try:
            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": text}]
            )
            await update.message.reply_text(response.choices[0].message.content)
        except Exception as e:
            await update.message.reply_text(f"Ошибка GPT:\n{e}")

# === /brief ===
async def brief_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришли .pdf или .docx с брифом — я сгенерирую 5 идей.")

# === Обработка документа ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith((".pdf", ".docx")):
        await update.message.reply_text("⚠️ Поддерживаются только .pdf и .docx файлы.")
        return

    await update.message.reply_text("📥 Загружаю файл…")

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
    except Exception as e:
        await update.message.reply_text(f"Ошибка загрузки файла:\n{e}")
        return

    # Извлекаем текст
    await update.message.reply_text("📄 Извлекаю текст из брифа…")
    try:
        if doc.file_name.endswith(".pdf"):
            text = extract_text_from_pdf(file_bytes)
        else:
            text = extract_text_from_docx(file_bytes)
    except Exception as e:
        await update.message.reply_text(f"Ошибка при чтении файла:\n{e}")
        return

    await update.message.reply_text("🧠 Отправляю бриф в GPT для генерации идей…")

    # Генерация через GPT
    gpt_prompt = f"""
Ты креативный директор. На основе этого брифа создай 5 креативных идей. Для каждой идеи:

1. Название  
2. Вводная часть  
3. Короткое описание  
4. Полное описание  
5. Реализация

Пункт 5 зависит от брифа:
- если видеоролик — сценарий (5.1)
- если 360 кампания — раскладка по каналам (5.2)
- если сиддинг — идеи инфлюенсеров (5.3)
- если ивент — формат мероприятия (5.4)

БРИФ:
{text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": gpt_prompt}]
        )
        result = response.choices[0].message.content
    except Exception as e:
        await update.message.reply_text(f"Ошибка генерации идей:\n{e}")
        return

    await update.message.reply_text("📄 Собираю PDF с результатом…")

    try:
        pdf_bytes = generate_pdf_with_logo(result)
        await update.message.reply_document(document=InputFile(pdf_bytes, filename="Креативные_идеи.pdf"))
    except Exception as e:
        await update.message.reply_text(f"Ошибка генерации PDF:\n{e}")

# === Чтение PDF ===
def extract_text_from_pdf(file_bytes):
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

# === Чтение DOCX ===
def extract_text_from_docx(file_bytes):
    f = io.BytesIO(file_bytes)
    doc = docx.Document(f)
    return "\n".join(p.text for p in doc.paragraphs)

# === PDF генерация ===
def generate_pdf_with_logo(text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # логотип
    logo_path = "/tmp/logo.png"
    convert_svg_logo("long logo h100.svg", logo_path)
    pdf.image(logo_path, x=10, y=10, w=40)
    pdf.set_xy(10, 40)
    pdf.set_font("Arial", size=12)

    for line in text.split("\n"):
        pdf.multi_cell(0, 10, line)

    buffer = io.BytesIO()
    pdf.output(buffer)
    buffer.seek(0)
    return buffer

# === SVG → PNG логотип ===
def convert_svg_logo(svg_path, png_path):
    if not os.path.exists(png_path):
        cairosvg.svg2png(url=svg_path, write_to=png_path)

# === Запуск ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", brief_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
