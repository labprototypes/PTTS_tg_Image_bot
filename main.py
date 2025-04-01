import os
import sys
import openai
import fitz  # PyMuPDF
from docx import Document
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fpdf import FPDF
from io import BytesIO
import atexit

# Создаём файл-замок, если бот уже запущен — выходим
lock_file = "/tmp/bot.lock"

if os.path.exists(lock_file):
    print("Бот уже запущен. Завершаем процесс.")
    sys.exit()

with open(lock_file, "w") as f:
    f.write("running")

# При завершении — удалим замок
atexit.register(lambda: os.remove(lock_file))

# Загрузка API ключей из переменных окружения
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

is_generating_ideas = False
is_active = True  # Флаг активности бота

# Асинхронный клиент для OpenAI (новая версия)
client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Функция для извлечения текста из PDF
def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text

# Функция для извлечения текста из DOCX
def extract_text_from_docx(file_path):
    doc = Document(file_path)
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

# GPT-4o генерация
async def generate_ideas_from_brief(brief_text: str) -> str:
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты сильный креативный директор. Генерируй креативные идеи строго по структуре."},
            {"role": "user", "content": f"Вот бриф:\n{brief_text}\nСгенерируй 5 идей. Формат:\n1. Название (крупно)\n2. Интро\n3. Кратко\n4. Подробно\n5. Сценарий\n6. Почему идея хорошая"}
        ],
        temperature=0.8,
        max_tokens=2500
    )
    return response.choices[0].message.content.strip()

# PDF генерация
def create_pdf(ideas: str) -> BytesIO:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Загрузка кастомного шрифта
    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdf.add_font("TTNorms", "", font_path, uni=True)
    pdf.set_font("TTNorms", size=12)

    for idx, idea in enumerate(ideas.split("\n\n"), start=1):
        pdf.set_font("TTNorms", size=16)
        pdf.cell(0, 10, f"Idea {idx}", ln=True)
        pdf.set_font("TTNorms", size=12)
        for line in idea.strip().split("\n"):
            pdf.multi_cell(0, 10, line)
        pdf.ln(5)

    pdf_output = BytesIO()
    pdf.output(pdf_output)
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

# Файл-бриф
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

    # Определяем формат файла и извлекаем текст
    if file_path.endswith(".pdf"):
        brief_text = extract_text_from_pdf(file_path)
    elif file_path.endswith(".docx"):
        brief_text = extract_text_from_docx(file_path)
    else:
        await update.message.reply_text("Формат файла не поддерживается. Пожалуйста, отправьте PDF или DOCX.")
        is_generating_ideas = False
        return

    # Генерация идей с использованием GPT-4o
    ideas = await generate_ideas_from_brief(brief_text)
    pdf_file = create_pdf(ideas)

    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово! Можем снова болтать 🙂")

    is_generating_ideas = False

# Свободное общение с ботом (если не в процессе генерации)
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
    answer = response.choices[0].message.content.strip()
    await update.message.reply_text(answer)

# Основной запуск
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), chat_mode))

    app.run_polling()

if __name__ == "__main__":
    main()
