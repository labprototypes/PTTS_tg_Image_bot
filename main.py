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

# === Защита от повторного запуска ===
lock_file = "/tmp/bot.lock"
if os.path.exists(lock_file):
    print("Бот уже запущен.")
    sys.exit()
with open(lock_file, "w") as f:
    f.write("running")
atexit.register(lambda: os.remove(lock_file))

# === Настройки ===
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

is_generating_ideas = False
is_active = True

# === Парсеры файлов ===
def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join(page.get_text() for page in doc)

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# === Генерация первичных идей GPT ===
async def generate_ideas_from_brief(brief_text: str) -> str:
    prompt = (
        "Ты выдающийся креативный директор. "
        "На основе брифа сгенерируй 5 насыщенных креативных идей.\n\n"
        "Формат:\n"
        "Идея 1: Название\n"
        "Интро: Вступление\n"
        "Кратко: Суть в одной фразе\n"
        "Подробно: Расширенная концепция\n"
        "Сценарий: Детальный сценарий видеоролика/механики\n"
        "Почему идея хорошая: Аргументация\n\n"
        "Пиши без markdown (*, #, ** и т.п.)."
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

# === Генерация идей с учётом правок ===
async def regenerate_ideas_with_feedback(original_ideas: str, comments: list[str]) -> str:
    feedback = "\n".join(f"- {c}" for c in comments)
    prompt = (
        "Улучшите существующие 5 идей с учётом следующих комментариев.\n\n"
        "Вот исходные идеи:\n"
        f"{original_ideas}\n\n"
        "Вот комментарии:\n"
        f"{feedback}\n\n"
        "Сгенерируй обновлённую версию в том же формате. Сделай текст более структурированным, развёрнутым, с чёткой логикой. Не используй markdown."
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=4000
    )

    return re.sub(r"[*#]+", "", response.choices[0].message.content.strip())

# === PDF генерация ===
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

            if re.match(r"^Идея \d+:", line):
                c.setFont("CustomFont", heading_size)
                for part in wrap(line, width=int(max_width / (heading_size * 0.55))):
                    c.drawString(margin_x, y, part)
                    y -= line_height
                y -= 10
                continue

            if any(line.startswith(h + ":") for h in ["Интро", "Кратко", "Подробно", "Сценарий", "Почему идея хорошая"]):
                header, _, rest = line.partition(":")
                c.setFont("CustomFont", subheading_size)
                c.drawString(margin_x, y, f"{header}:")
                y -= line_height

                c.setFont("CustomFont", font_size)
                if header in ["Сценарий", "Почему идея хорошая"]:
                    points = re.split(r"(?<=[.!?])\s+(?=\w)", rest.strip())
                    for point in points:
                        point = "– " + point.strip()
                        for part in wrap(point, width=int(max_width / (font_size * 0.55))):
                            if y < 60:
                                c.showPage()
                                y = height - 50
                                c.setFont("CustomFont", font_size)
                            c.drawString(margin_x + 10, y, part)
                            y -= line_height
                        y -= 4
                else:
                    for part in wrap(rest.strip(), width=int(max_width / (font_size * 0.55))):
                        if y < 60:
                            c.showPage()
                            y = height - 50
                        c.drawString(margin_x + 10, y, part)
                        y -= line_height
                    y -= 5

                y -= 10
                continue

            for part in wrap(line, width=int(max_width / (font_size * 0.55))):
                if y < 60:
                    c.showPage()
                    y = height - 50
                c.drawString(margin_x, y, part)
                y -= line_height
            y -= 5

    c.save()
    pdf_output.seek(0)
    return pdf_output

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    is_active = True
    await update.message.reply_text("Привет! Отправь бриф (PDF/DOCX), и я сгенерирую идеи.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен.")

# === Файл-бриф ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas
    if is_generating_ideas:
        await update.message.reply_text("Пожалуйста, подожди. Я уже думаю над другим брифом.")
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
    context.user_data["ideas_raw"] = ideas
    context.user_data["review_mode"] = True
    context.user_data["comments"] = []

    pdf_file = create_pdf(ideas)
    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово! Напиши **Принять** или **Дать правки**.")

    is_generating_ideas = False

# === Чат-режим и обработка правок ===
async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if context.user_data.get("review_mode"):
        if text == "принять":
            context.user_data.clear()
            await update.message.reply_text("Отлично! Работа завершена. Жду следующий бриф.")
            return
        elif text == "дать правки":
            context.user_data["collecting_feedback"] = True
            await update.message.reply_text("Ок! Напиши комментарии. Когда закончишь, напиши **Готово**.")
            return
        elif context.user_data.get("collecting_feedback"):
            if text == "готово":
                context.user_data["collecting_feedback"] = False
                await update.message.reply_text("Спасибо! Применяю правки...")
                await regenerate_and_send(update, context)
                return
            else:
                context.user_data["comments"].append(update.message.text.strip())
                await update.message.reply_text("Комментарий сохранён. Добавь ещё или напиши **Готово**.")
                return

    # Обычный чат
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": update.message.text}
        ],
        temperature=0.7,
        max_tokens=800
    )
    await update.message.reply_text(response.choices[0].message.content.strip())

# === Регенерация PDF после правок ===
async def regenerate_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas
    is_generating_ideas = True

    updated_ideas = await regenerate_ideas_with_feedback(
        context.user_data["ideas_raw"],
        context.user_data["comments"]
    )
    context.user_data["ideas_raw"] = updated_ideas
    context.user_data["comments"] = []

    pdf_file = create_pdf(updated_ideas)
    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas_updated.pdf"))
    await update.message.reply_text("Вот обновлённый файл! Напиши **Принять** или **Дать правки**.")
    is_generating_ideas = False

# === Старт бота ===
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), chat_mode))
    app.run_polling()

if __name__ == "__main__":
    main()
