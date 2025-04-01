import logging
import os
import tempfile
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from docx import Document
import pdfplumber
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка шрифта
FONT_PATH = "TT Travels Next Trial Bold.ttf"
FONT_SIZE = 72

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# 🔧 Фейковый HTTP-сервер для Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_fake_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# 🚀 Обработчик команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь .docx или .pdf файл, и я сгенерирую слоган.")

# 📄 Обработка документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    try:
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "Ты дизайнер. Сформулируй фразу в стиле слогана по тексту."},
                {"role": "user", "content": text[:2000]}
            ]
        )
        slogan = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await update.message.reply_text("Ошибка при обращении к GPT.")
        return

    image_path = generate_image_with_text(slogan)

    with open(image_path, "rb") as img_file:
        await update.message.reply_photo(photo=InputFile(img_file), caption="Ваш слоган 👆")

# 📄 Поддержка форматов
def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def extract_text_from_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text

# 🖼️ Генерация изображения
def generate_image_with_text(text):
    width, height = 1080, 1080
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    lines = []
    words = text.split()
    line = ""
    for word in words:
        if draw.textlength(line + " " + word, font=font) < width - 100:
            line += " " + word
        else:
            lines.append(line.strip())
            line = word
    lines.append(line.strip())

    y = (height - len(lines) * (FONT_SIZE + 20)) // 2
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (width - line_width) // 2
        draw.text((x, y), line, fill="black", font=font)
        y += FONT_SIZE + 20

    path = os.path.join(tempfile.gettempdir(), "output.jpg")
    image.save(path, "JPEG")
    return path

# ▶️ Запуск
if __name__ == "__main__":
    import asyncio

    async def run_bot():
        TOKEN = os.environ["BOT_TOKEN"]
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

        logger.info("Бот запускается...")

        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

    # Запуск фейк-сервера и бота
    Thread(target=run_fake_server).start()
    asyncio.run(run_bot())
