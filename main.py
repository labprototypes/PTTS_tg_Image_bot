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
from PIL import Image
import cairosvg

load_dotenv()

# === Настройки ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# === Telegram log ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я могу:\n- Ответить на вопросы\n- Нарисовать картинку (`Картинка: ...`)\n- Обработать бриф через `/бриф`")

# === Обработка текстовых сообщений ===
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
            await update.message.reply_text(f"Ошибка генерации изображения: {e}")
    else:
        try:
            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": text}]
            )
            await update.message.reply_text(response.choices[0].message.content)
        except Exception as e:
            await update.message.reply_text(f"Ошибка GPT: {e}")

# === Команда /бриф ===
async def bried_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пожалуйста, отправь .pdf или .docx файл с брифом.")

# === Обработка документа ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith((".pdf", ".docx")):
        await update.message.reply_text("Поддерживаются только .pdf и .docx файлы.")
        return

    await update.message.reply_text("📑 Обрабатываю файл и генерирую идеи...")

    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()

    # Извлекаем текст из файла
    if doc.file_name.endswith(".pdf"):
        text = extract_text_from_pdf(file_bytes)
    else:
        text = extract_text_from_docx(file_bytes)

    # Формируем запрос к GPT
    gpt_prompt = f"""
Ты креативный директор рекламного агентства. Ниже приведён бриф. На его основе сгенерируй 5 уникальных креативных идей. Каждая идея должна содержать:

1. Название идеи  
2. Вводную часть  
3. Короткое описание идеи  
4. Полное описание идеи  
5. Реализация идеи

Пункт 5 должен меняться в зависимости от задачи в брифе:

- Если в брифе указано, что это видеоролик — предложи сценарий (5.1)
- Если это 360 кампания / общая идея — предложи идею и её адаптацию под каналы (5.2)
- Если это сиддинг / инфлюенсеры — предложи варианты (5.3)
- Если это ивент — предложи реализацию мероприятия (5.4)

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
        await update.message.reply_text(f"Ошибка генерации идей: {e}")
        return

    # Генерируем PDF
    pdf_bytes = generate_pdf_with_logo(result)

    await update.message.reply_document(document=InputFile(pdf_bytes, filename="ideas.pdf"))

# === Извлечение текста ===
def extract_text_from_pdf(file_bytes):
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

def extract_text_from_docx(file_bytes):
    f = io.BytesIO(file_bytes)
    doc = docx.Document(f)
    return "\n".join(p.text for p in doc.paragraphs)

# === Генерация PDF с логотипом ===
def generate_pdf_with_logo(text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Вставка логотипа
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

# === Конвертация SVG логотипа в PNG ===
def convert_svg_logo(svg_path, png_path):
    if not os.path.exists(png_path):
        cairosvg.svg2png(url=svg_path, write_to=png_path)

# === Запуск приложения ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("бриф", bried_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
