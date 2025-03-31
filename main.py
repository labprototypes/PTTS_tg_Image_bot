import os
import logging
from telegram import Update, Document
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv
import openai
import docx
import pdfplumber
from fpdf import FPDF

# === Загрузка переменных из Render (через os.environ) ===
TOKEN = os.environ.get("TOKEN")
openai.api_key = os.environ.get("OPENAI_API_KEY")

# === Константы ===
FONT_PATH = "TT Travels Next Trial Bold.ttf"
OUTPUT_PDF = "ideas_output.pdf"

# === Логгирование ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь .docx или .pdf файл и напиши /brief для генерации идей"
    )

# === Обработка загрузки документа ===
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file: Document = update.message.document
    file_path = await file.get_file()

    file_ext = file.file_name.lower().split('.')[-1]
    if file_ext not in ["docx", "pdf"]:
        await update.message.reply_text("Поддерживаются только .docx и .pdf файлы.")
        return

    local_filename = f"brief.{file_ext}"
    await file_path.download_to_drive(local_filename)

    context.user_data["brief_path"] = local_filename
    context.user_data["brief_type"] = file_ext

    await update.message.reply_text("Файл получен. Напишите /brief для генерации идей.")

# === Генерация идей на основе брифа ===
async def generate_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "brief_path" not in context.user_data:
        await update.message.reply_text("Сначала отправьте файл .docx или .pdf.")
        return

    path = context.user_data["brief_path"]
    ext = context.user_data["brief_type"]

    await update.message.reply_text("Извлекаю текст из брифа...")

    try:
        if ext == "docx":
            doc = docx.Document(path)
            full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif ext == "pdf":
            with pdfplumber.open(path) as pdf:
                full_text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
        else:
            full_text = ""
    except Exception as e:
        logger.error(f"Ошибка чтения файла: {e}")
        await update.message.reply_text("Не удалось извлечь текст.")
        return

    if not full_text.strip():
        await update.message.reply_text("Файл пустой или не удалось извлечь текст.")
        return

    await update.message.reply_text("Генерирую идеи через GPT...")

    try:
        prompt = f"Вот бриф:\n\n{full_text}\n\nСгенерируй 5 креативных идей на основе этого брифа."
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=800,
        )
        ideas = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await update.message.reply_text("Ошибка при генерации идей.")
        return

    await update.message.reply_text("Формирую PDF...")

    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.add_font("CustomFont", "", FONT_PATH, uni=True)
        pdf.set_font("CustomFont", size=12)

        for line in ideas.split("\n"):
            pdf.multi_cell(0, 10, line)

        pdf.output(OUTPUT_PDF)
        with open(OUTPUT_PDF, "rb") as f:
            await update.message.reply_document(document=f, filename=OUTPUT_PDF)
    except Exception as e:
        logger.error(f"Ошибка генерации PDF: {e}")
        await update.message.reply_text(f"Ошибка PDF: {e}")

# === Основной запуск ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(CommandHandler("brief", generate_brief))

    app.run_polling()
