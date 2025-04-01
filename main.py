import os
import sys
import openai
from docx import Document
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from io import BytesIO
import atexit
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from textwrap import wrap
import fitz  # PyMuPDF

# Файл-замок
lock_file = "/tmp/bot.lock"
if os.path.exists(lock_file):
    print("Бот уже запущен. Завершаем процесс.")
    sys.exit()
with open(lock_file, "w") as f:
    f.write("running")
atexit.register(lambda: os.remove(lock_file))

# Ключи
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

is_generating_ideas = False
is_active = True

# PDF → текст
def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join(page.get_text() for page in doc)

# DOCX → текст
def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# Генерация идей
async def generate_ideas_from_brief(brief_text: str) -> str:
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты сильный креативный директор. Генерируй креативные идеи строго по структуре."},
            {"role": "user", "content": f"Вот бриф:\n{brief_text}\nСгенерируй ровно 5 идей. Формат:\n1. Название (крупно)\n2. Интро\n3. Кратко\n4. Подробно\n5. Сценарий\n6. Почему идея хорошая"}
        ],
        temperature=0.8,
        max_tokens=2500
    )
    return response.choices[0].message.content.strip()

# PDF генерация
def create_pdf(ideas: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont('CustomFont', font_path))

    margin_left = 50
    margin_right = 50
    max_line_width = width - margin_left - margin_right
    font_size = 12
    line_height = 16

    y_position = height - 50

    for idx, idea in enumerate(ideas.strip().split("\n\n"), start=1):
        c.setFont("CustomFont", 16)
        c.drawString(margin_left, y_position, f"Idea {idx}")
        y_position -= 24

        c.setFont("CustomFont", font_size)
        lines = idea.strip().split("\n")

        for line in lines:
            wrapped = wrap(line, width=int(max_line_width / (font_size * 0.55)))
            for part in wrapped:
                if y_position < 50:
                    c.showPage()
                    c.setFont("CustomFont", font_size)
                    y_position = height - 50
                c.drawString(margin_left, y_position, part)
                y_position -= line_height
            y_position -= 4
        y_position -= 20

    c.save()
    pdf_output.seek(0)
    return pdf_output

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот был остановлен. Для продолжения работы отправь /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Сейчас я генерирую идеи. Подожди немного.")
    else:
        await update.message.reply_text("Привет! Ты можешь просто поговорить со мной, или отправь бриф в PDF/DOC — и я сгенерирую идеи.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Для продолжения работы отправь /start.")

# Обработка документа
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот был остановлен. Для продолжения работы отправь /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Подожди, я еще обрабатываю предыдущий бриф.")
        return

    is_generating_ideas = True
    await update.message.reply_text("Бриф получен. Читаю и думаю...")

    document = update.message.document
    file = await document.get_file()
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)

    if file_path.endswith(".pdf"):
        brief_text = extract_text_from_pdf(file_path)
    elif file_path.endswith(".docx"):
        brief_text = extract_text_from_docx(file_path)
    else:
        await update.message.reply_text("Формат файла не поддерживается. Пожалуйста, отправьте PDF или DOCX.")
        is_generating_ideas = False
        return

    ideas = await generate_ideas_from_brief(brief_text)
    pdf_file = create_pdf(ideas)

    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово! Можем снова болтать 🙂")

    is_generating_ideas = False

# Свободный диалог
async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот был остановлен. Для продолжения работы отправь /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Секунду, я еще думаю над идеями. Скоро вернусь!")
        return

    user_message = update.message.text
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты умный и доброжелательный ассистент, общайся в свободной форме."},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7,
        max_tokens=800
    )
    await update.message.reply_text(response.choices[0].message.content.strip())

# Запуск бота
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), chat_mode))
    app.run_polling()

if __name__ == "__main__":
    main()
