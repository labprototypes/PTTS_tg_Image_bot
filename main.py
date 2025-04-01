import os
import sys
import openai
import re
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
    prompt = (
        "Ты сильный креативный директор. "
        "Твоя задача — на основе брифа сгенерировать 5 мощных, полноценных креативных идей. "
        "Они должны быть развёрнутыми, детализированными, с ясной драматургией и аргументацией.\n\n"
        "Формат каждой идеи:\n"
        "1. Идея N: Название (в первой строке)\n"
        "2. Интро — эмоциональное вступление\n"
        "3. Кратко — одна суть/фраза\n"
        "4. Подробно — расширенная идея (5–8 строк)\n"
        "5. Сценарий — пошаговый план, как выглядит ролик или механика (8–10 строк)\n"
        "6. Почему идея хорошая — аргументы с позиции бренда и потребителя\n\n"
        f"Вот бриф:\n{brief_text}\n\n"
        "Не используй символы форматирования вроде * или #. Пиши чисто и по делу. Сгенерируй ровно 5 идей."
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты — креативный директор."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.9,
        max_tokens=3000
    )

    cleaned = re.sub(r"[\\*#]+", "", response.choices[0].message.content.strip())
    return cleaned

# PDF генерация с переносами и полями
def create_pdf(ideas: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont('CustomFont', font_path))

    margin_left = 45
    margin_right = 45
    max_line_width = width - margin_left - margin_right
    font_size = 11.5
    line_height = 15

    y_position = height - 50
    c.setFont("CustomFont", font_size)

    # Идеи начинаются с "Идея N:"
    ideas_list = re.split(r"(?=\n?Идея \d+:)", ideas.strip())

    for idea_block in ideas_list:
        lines = idea_block.strip().split("\n")
        for line in lines:
            if re.match(r"^Идея \d+:", line):
                c.setFont("CustomFont", 16)
                wrapped = wrap(line, width=int(max_line_width / (16 * 0.55)))
            else:
                c.setFont("CustomFont", font_size)
                wrapped = wrap(line, width=int(max_line_width / (font_size * 0.55)))

            for part in wrapped:
                if y_position < 50:
                    c.showPage()
                    y_position = height - 50
                    c.setFont("CustomFont", font_size)
                c.drawString(margin_left, y_position, part)
                y_position -= line_height

            y_position -= 5
        y_position -= 20

    c.save()
    pdf_output.seek(0)
    return pdf_output

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот был остановлен. Для продолжения работы отправь /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Сейчас я генерирую идеи. Подожди немного.")
    else:
        await update.message.reply_text("Привет! Ты можешь просто поговорить со мной, или отправь бриф в PDF/DOC — и я сгенерирую идеи.")

# /stop
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Для продолжения работы отправь /start.")

# Бриф-файл
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

# Чат-режим
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

# Запуск
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), chat_mode))
    app.run_polling()

if __name__ == "__main__":
    main()
