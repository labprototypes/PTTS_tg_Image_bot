import os
import logging
from telegram import Update, Document
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from openai import OpenAI
from docx import Document as DocxDocument
import pdfplumber

# === ЛОГГИРОВАНИЕ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === ИНИЦИАЛИЗАЦИЯ OpenAI ===
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# === ОБРАБОТКА ДОКУМЕНТА ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file: Document = update.message.document
    file_name = file.file_name

    if not file_name.lower().endswith(('.pdf', '.docx')):
        await update.message.reply_text("Пожалуйста, отправь файл в формате .docx или .pdf.")
        return

    file_path = await file.get_file()
    file_data = await file_path.download_as_bytearray()

    try:
        if file_name.endswith(".pdf"):
            text = extract_text_from_pdf(file_data)
        else:
            text = extract_text_from_docx(file_data)

        await update.message.reply_text("Генерирую идеи на основе брифа... 💡")

        # Запрос к OpenAI (новый синтаксис)
        response = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "Ты креативный ассистент, который генерирует маркетинговые идеи на основе брифа."},
                {"role": "user", "content": text}
            ],
            temperature=0.8,
            max_tokens=1000
        )

        ideas = response.choices[0].message.content
        await update.message.reply_text(ideas)

    except Exception as e:
        logger.error("Ошибка обработки файла или GPT: %s", e)
        await update.message.reply_text("Произошла ошибка при обработке. Попробуй ещё раз позже.")

# === EXTRACT DOCX ===
def extract_text_from_docx(file_data: bytearray) -> str:
    from io import BytesIO
    doc = DocxDocument(BytesIO(file_data))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip() != ""])

# === EXTRACT PDF ===
def extract_text_from_pdf(file_data: bytearray) -> str:
    from io import BytesIO
    with pdfplumber.open(BytesIO(file_data)) as pdf:
        return "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])

# === START БОТА ===
if __name__ == "__main__":
    TOKEN = os.environ["BOT_TOKEN"]

    app = ApplicationBuilder().token(TOKEN).build()

    doc_handler = MessageHandler(filters.Document.ALL, handle_document)
    app.add_handler(doc_handler)

    logger.info("Бот запущен.")
    app.run_polling()
