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

import fitz  # PyMuPDF

# === Файл-замок, чтобы не запускался второй процесс ===
lock_file = "/tmp/bot.lock"

if os.path.exists(lock_file):
    print("Бот уже запущен. Завершаем процесс.")
    sys.exit()
with open(lock_file, "w") as f:
    f.write("running")
atexit.register(lambda: os.remove(lock_file))

# === Ключи и переменные ===
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
is_generating_ideas = False
is_active = True

client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# === Чтение файлов ===
def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])

# === Генерация идей ===
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

# === PDF генерация ===
def create_pdf(ideas: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
    c.setFont("CustomFont", 12)

    y = height - 40
    ideas_list = ideas.split("\nIdea")

    for idx, idea in enumerate(ideas_list[1:], start=1):
        c.setFont("CustomFont", 16)
        c.drawString(40, y, "Idea {}: {}".format(idx, idea.split('\n')[0]))
        y -= 20

        c.setFont("CustomFont", 12)
        sections = ['Интро', 'Кратко', 'Подробно', 'Сценарий', 'Почему идея хорошая']

        for section in sections:
            c.setFont("CustomFont", 14)
            c.drawString(40, y, f"{section}:")
            y -= 15

            c.setFont("CustomFont", 12)
            section_text = [line for line in idea.split('\n') if line.startswith(section)]
            if section_text:
                lines = section_text[0].split(":", 1)[1].strip().split(". ")
                for line in lines:
                    text = line.strip()
                    if text:
                        c.drawString(40, y, text)
                        y -= 14

            y -= 10

        y -= 20
        if y < 80:
            c.showPage()
            c.setFont("CustomFont", 12)
            y = height - 40

    c.save()
    pdf_output.seek(0)
    return pdf_output

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    is_active = True
    is_generating_ideas = False
    await update.message.reply_text("Бот активен. Жду бриф или просто пиши.")

# === /stop ===
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Чтобы продолжить — /start.")

# === Получение документа ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот остановлен. Чтобы продолжить — /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Уже работаю над предыдущим брифом.")
        return

    is_generating_ideas = True
    await update.message.reply_text("Читаю бриф...")

    doc = update.message.document
    file = await doc.get_file()
    path = f"/tmp/{doc.file_name}"
    await file.download_to_drive(path)

    if path.endswith(".pdf"):
        brief_text = extract_text_from_pdf(path)
    elif path.endswith(".docx"):
        brief_text = extract_text_from_docx(path)
    else:
        await update.message.reply_text("Поддерживаются только PDF и DOCX.")
        is_generating_ideas = False
        return

    # === Генерация идей и лог ===
    ideas = await generate_ideas_from_brief(brief_text)
    print("\n\n=== ИДЕИ, полученные от GPT-4o ===\n")
    print(ideas)
    print("\n=== КОНЕЦ ИДЕЙ ===\n")

    if not ideas.strip():
        await update.message.reply_text("GPT не вернул идей 😢")
        is_generating_ideas = False
        return

    pdf_file = create_pdf(ideas)
    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово. Можем снова общаться.")
    is_generating_ideas = False

# === Чат с ботом ===
async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот выключен. Напиши /start чтобы включить.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Секунду, еще обрабатываю бриф...")
        return

    user_message = update.message.text
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты доброжелательный и умный ассистент. Общайся с пользователем в свободной форме."},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7,
        max_tokens=800
    )
    reply = response.choices[0].message.content.strip()
    await update.message.reply_text(reply)

# === MAIN ===
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), chat_mode))
    app.run_polling()

if __name__ == "__main__":
    main()
