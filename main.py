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

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join(page.get_text() for page in doc)

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

async def generate_ideas_from_brief(brief_text: str) -> str:
    prompt = (
        "Ты выдающийся креативный директор. "
        "На основе брифа сгенерируй 5 насыщенных креативных идей.\n\n"
        "Формат каждой:\n"
        "Идея 1: Название\n"
        "Интро: Вступление\n"
        "Кратко: Суть в одной фразе\n"
        "Подробно: Расширенная концепция\n"
        "Сценарий: Детальный сценарий видеоролика/механики\n"
        "Почему идея хорошая: Аргументация — бренд, аудитория, ценности\n\n"
        "Пиши без markdown (*, #, ** и т.п.). Разделяй каждый блок с новой строки."
        f"\n\nВот бриф:\n{brief_text}"
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты креативный директор."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.9,
        max_tokens=4000
    )

    return re.sub(r"[*#]+", "", response.choices[0].message.content.strip())

# PDF генерация
def create_pdf(ideas: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont('CustomFont', font_path))

    margin_x = 50
    max_width = width - 2 * margin_x
    font_size = 11.5
    heading_size = 16
    subheading_size = 13
    line_height = 15
    y = height - 50

    c.setFont("CustomFont", font_size)

    # Разделение по идеям
    ideas_list = re.split(r"(?=\n?Идея \d+:)", ideas.strip())
    for idx, idea in enumerate(ideas_list):
        if idx > 0:
            c.showPage()
            y = height - 50

        lines = idea.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Заголовок идеи
            if re.match(r"^Идея \d+:", line):
                c.setFont("CustomFont", heading_size)
                wrapped = wrap(line, width=int(max_width / (heading_size * 0.55)))
            # Подзаголовки
            elif any(line.startswith(h + ":") for h in ["Интро", "Кратко", "Подробно", "Сценарий", "Почему идея хорошая"]):
                c.setFont("CustomFont", subheading_size)
                header, _, rest = line.partition(":")
                wrapped = wrap(f"{header}:", width=int(max_width / (subheading_size * 0.55)))
                for part in wrapped:
                    if y < 50:
                        c.showPage()
                        y = height - 50
                    c.drawString(margin_x, y, part)
                    y -= line_height
                c.setFont("CustomFont", font_size)
                wrapped_text = wrap(rest.strip(), width=int(max_width / (font_size * 0.55)))
                for part in wrapped_text:
                    if y < 50:
                        c.showPage()
                        y = height - 50
                    c.drawString(margin_x + 10, y, part)
                    y -= line_height
                continue
            else:
                c.setFont("CustomFont", font_size)
                wrapped = wrap(line, width=int(max_width / (font_size * 0.55)))

            for part in wrapped:
                if y < 50:
                    c.showPage()
                    y = height - 50
                c.drawString(margin_x, y, part)
                y -= line_height

            y -= 5

    c.save()
    pdf_output.seek(0)
    return pdf_output

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот остановлен. Напиши /start для активации.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Я ещё думаю над идеями. Подожди немного.")
    else:
        await update.message.reply_text("Привет! Можешь отправить бриф (PDF/DOCX), и я сгенерирую идеи. Или просто поболтаем 🙂")

# /stop
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Для старта — /start")

# Бриф
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот остановлен. Напиши /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Я уже обрабатываю один бриф. Подожди немного.")
        return

    is_generating_ideas = True
    await update.message.reply_text("Бриф получен. Думаю...")

    document = update.message.document
    file = await document.get_file()
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)

    if file_path.endswith(".pdf"):
        brief_text = extract_text_from_pdf(file_path)
    elif file_path.endswith(".docx"):
        brief_text = extract_text_from_docx(file_path)
    else:
        await update.message.reply_text("Поддерживаются только PDF и DOCX.")
        is_generating_ideas = False
        return

    ideas = await generate_ideas_from_brief(brief_text)
    pdf_file = create_pdf(ideas)

    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово ✅")
    is_generating_ideas = False

# Чат
async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active:
        await update.message.reply_text("Бот выключен. Напиши /start.")
        return
    if is_generating_ideas:
        await update.message.reply_text("Секунду, я сейчас занят генерацией идей.")
        return

    user_message = update.message.text
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты умный и дружелюбный ассистент, общайся в свободной форме."},
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
