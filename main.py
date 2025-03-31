import os
import logging
from telegram import Update, Document
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv
import openai
import docx
from fpdf import FPDF

# === Настройка ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Подгружаем переменные окружения
TOKEN = os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

FONT_PATH = "TT Travels Next Trial Bold.ttf"
OUTPUT_PDF = "ideas.pdf"

# === Обработчики ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь .docx файл и напиши /brief")

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    if file.mime_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        await update.message.reply_text("Отправьте .docx файл, пожалуйста.")
        return

    file_path = await file.get_file()
    file_name = "brief.docx"
    await file_path.download_to_drive(file_name)

    context.user_data["brief_path"] = file_name
    await update.message.reply_text("Файл получен. Напишите /brief для генерации идей.")

async def generate_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "brief_path" not in context.user_data:
        await update.message.reply_text("Сначала загрузите .docx файл.")
        return

    await update.message.reply_text("Извлекаю текст из брифа...")

    try:
        doc = docx.Document(context.user_data["brief_path"])
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception as e:
        logger.error(f"Ошибка чтения документа: {e}")
        await update.message.reply_text("Не удалось прочитать документ.")
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

# === Запуск ===

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", generate_brief))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))

    logger.info("Бот запущен...")
    app.run_polling()
