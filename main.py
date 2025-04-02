import os
import sys
import openai
import fitz
import atexit
import re
from io import BytesIO
from docx import Document
from textwrap import wrap
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

# Файл-замок, чтобы запускался только один процесс
lock_file = "/tmp/bot.lock"
if os.path.exists(lock_file):
    print("Бот уже запущен. Завершаем процесс.")
    sys.exit()
with open(lock_file, "w") as f:
    f.write("running")
atexit.register(lambda: os.remove(lock_file))

# API ключи
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
is_generating_ideas = False
is_active = True
pending_briefs = {}

# Чтение документов
def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join([page.get_text() for page in doc])

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# Генерация идей
async def generate_ideas(brief_text, extra_comment=None):
    prompt = f"Вот бриф:\n{brief_text}"
    if extra_comment:
        prompt += f"\n\nКомментарий к брифу:\n{extra_comment}"
    prompt += "\nСгенерируй РОВНО 5 идей. Формат:\n1. Название (крупно)\n2. Интро (2 абзаца)\n3. Кратко\n4. Подробно (2 абзаца)\n5. Сценарий (5 пунктов)\n6. Почему идея хорошая (3 пункта)"

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты сильный креативный директор. Пиши строго по формату."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.85,
        max_tokens=2800
    )
    return response.choices[0].message.content.strip()

# PDF генерация
def create_pdf(ideas_text: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter
    x_margin, y = 50, height - 50
    max_width = width - x_margin * 2 - 10

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont("CustomFont", font_path))
    c.setFont("CustomFont", 12)

    heading_size = 16
    subheading_size = 13
    body_size = 11.5
    line_height = 15

    ideas = re.split(r"(?=Идея \d+:)", ideas_text.strip())

    for idea in ideas:
        lines = idea.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            if re.match(r"^Идея \d+:", line):
                c.setFont("CustomFont", heading_size)
                for part in wrap(line, width=int(max_width / (heading_size * 0.55))):
                    c.drawString(x_margin, y, part)
                    y -= line_height
                y -= 10
                continue

            if any(line.startswith(h + ":") for h in ["Интро", "Кратко", "Подробно", "Сценарий", "Почему идея хорошая"]):
                header, _, rest = line.partition(":")
                c.setFont("CustomFont", subheading_size)
                c.drawString(x_margin, y, f"{header}:")
                y -= line_height
                c.setFont("CustomFont", body_size)

                # Обработка пунктов
                if header in ["Сценарий", "Почему идея хорошая"]:
                    numbered = re.findall(r"\d+\.\s.+", idea)
                    for p in numbered:
                        wrapped = wrap(p, width=int(max_width / (body_size * 0.55)))
                        for wline in wrapped:
                            c.drawString(x_margin + 10, y, wline)
                            y -= line_height
                        y -= 2
                else:
                    for part in wrap(rest.strip(), width=int(max_width / (body_size * 0.55))):
                        c.drawString(x_margin, y, part)
                        y -= line_height
                y -= 10
                continue

            # Обычный текст
            for wrapped_line in wrap(line, width=int(max_width / (body_size * 0.55))):
                c.drawString(x_margin, y, wrapped_line)
                y -= line_height
            y -= 4

        y -= 40  # отступ между идеями
        if y < 100:
            c.showPage()
            c.setFont("CustomFont", 12)
            y = height - 50

    c.save()
    pdf_output.seek(0)
    return pdf_output

# Telegram Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = True
    await update.message.reply_text("Привет! Отправь бриф в PDF или DOCX, и я предложу 5 креативных идей. Можно также добавить сопроводительное сообщение.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Чтобы запустить заново, отправь /start.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active or is_generating_ideas:
        return

    document = update.message.document
    user_id = update.message.from_user.id
    file = await document.get_file()
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)

    if file_path.endswith(".pdf"):
        brief_text = extract_text_from_pdf(file_path)
    elif file_path.endswith(".docx"):
        brief_text = extract_text_from_docx(file_path)
    else:
        await update.message.reply_text("Поддерживаются только PDF и DOCX.")
        return

    pending_briefs[user_id] = {
        "brief": brief_text,
        "state": "waiting_for_comment"
    }

    await update.message.reply_text("Бриф получен ✅\nДобавить сопроводительное сообщение? Напиши его сейчас или ответь 'нет'.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas
    user_id = update.message.from_user.id
    if user_id in pending_briefs:
        entry = pending_briefs.pop(user_id)
        brief = entry["brief"]
        extra_comment = update.message.text.strip() if update.message.text.lower() != "нет" else None

        await update.message.reply_text("Спасибо! Работаю над идеями 🧠💡")
        is_generating_ideas = True

        ideas = await generate_ideas(brief, extra_comment)
        pdf_file = create_pdf(ideas)

        await update.message.reply_document(InputFile(pdf_file, filename="ideas.pdf"))
        await update.message.reply_text(
            "Готово! Выберите, что делать дальше:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Принять ✅", callback_data="accept")],
                [InlineKeyboardButton("Комментировать 💬", callback_data="comment")]
            ])
        )
        is_generating_ideas = False
    else:
        await update.message.reply_text("Можем поговорить 🙂 Или отправь бриф, чтобы я придумал идеи.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "accept":
        await query.edit_message_text("Отлично! Идеи приняты 🎉")
    elif query.data == "comment":
        await query.edit_message_text("Окей, отправь комментарии. Напиши, хочешь доработать текущие идеи или сделать всё заново.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
