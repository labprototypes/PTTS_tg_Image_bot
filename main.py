import os
import sys
import re
import fitz
import openai
from docx import Document
from io import BytesIO
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler
)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from textwrap import wrap
import atexit
from collections import defaultdict

# === Блокировка запуска нескольких процессов ===
lock_file = "/tmp/bot.lock"
if os.path.exists(lock_file):
    sys.exit()
with open(lock_file, "w") as f:
    f.write("locked")
atexit.register(lambda: os.remove(lock_file))

# === Настройки OpenAI и Telegram ===
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
client = openai.AsyncOpenAI()

is_generating_ideas = False
is_active = True
awaiting_caption = {}
brief_context = {}
comments_context = defaultdict(list)

# === Обработка документов ===
def extract_text_from_pdf(file_path):
    return "\n".join(page.get_text() for page in fitz.open(file_path))

def extract_text_from_docx(file_path):
    return "\n".join([para.text for para in Document(file_path).paragraphs])

# === Генерация идей ===
async def generate_ideas_from_brief(brief_text: str, instructions: str = "") -> str:
    prompt = (
        "Ты сильный креативный директор. Сгенерируй ровно 5 креативных идей по брифу.\n"
        "Формат каждой идеи:\n"
        "Идея 1: Название\n"
        "Интро: минимум 2 абзаца\n"
        "Кратко: 1 фраза\n"
        "Подробно: минимум 2 абзаца\n"
        "Сценарий: минимум 5 подпунктов\n"
        "Почему идея хорошая: минимум 3 подпункта\n"
        "Не используй * или # или лишние тире.\n\n"
        f"Доп. вводная: {instructions}\n\nБриф:\n{brief_text}"
    )
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=4000
    )
    return re.sub(r"[*#]+", "", response.choices[0].message.content.strip())

async def regenerate_ideas(original: str, comments: list[str], rewrite_all: bool) -> str:
    prompt = (
        f"{'Перегенерируй полностью' if rewrite_all else 'Улучши'} идеи с учётом:\n"
        f"{chr(10).join(['- ' + c for c in comments])}\n\nОригинальные идеи:\n{original}"
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
    margin_x = 50
    max_width = width - 2 * margin_x - 30
    y = height - 50

    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont("CustomFont", font_path))
    c.setFont("CustomFont", 12)

    heading_size, subheading_size, font_size, line_height = 16, 13, 11.5, 15

    ideas_list = re.split(r"(?=\n?Идея \d+:)", ideas.strip())
    for idx, idea in enumerate(ideas_list):
        if idx > 0:
            y -= 50

        lines = idea.strip().split("\n")
        skip_empty = False
        for line in lines:
            line = line.strip()
            if not line:
                if skip_empty:
                    skip_empty = False
                    continue
                else:
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
                
                # Добавим отступ перед "Почему идея хорошая"
                if header == "Почему идея хорошая":
                    y -= 10

                c.setFont("CustomFont", subheading_size)
                c.drawString(margin_x, y, f"{header}:")
                y -= line_height
                c.setFont("CustomFont", font_size)
                if header in ["Сценарий", "Почему идея хорошая"]:
                    points = re.findall(r"(?:\d+[.)]|[-–•])?\s*(.+?)(?=(?:\d+[.)]|[-–•])\s+|$)", rest.strip(), re.DOTALL)
                    points = [p.strip() for p in points if p.strip()]
                    for i, item in enumerate(points, 1):
                        bullet = f"{i}. {item}"
                        for part in wrap(bullet, width=int(max_width / (font_size * 0.55))):
                            if y < 60:
                                c.showPage()
                                y = height - 50
                                c.setFont("CustomFont", font_size)
                            c.drawString(margin_x + 10, y, part)
                            y -= line_height
                    skip_empty = True
                else:
                    for part in wrap(rest.strip(), width=int(max_width / (font_size * 0.55))):
                        if y < 60:
                            c.showPage()
                            y = height - 50
                            c.setFont("CustomFont", font_size)
                        c.drawString(margin_x + 10, y, part)
                        y -= line_height
                continue

            for part in wrap(line, width=int(max_width / (font_size * 0.55))):
                if y < 60:
                    c.showPage()
                    y = height - 50
                    c.setFont("CustomFont", font_size)
                c.drawString(margin_x, y, part)
                y -= line_height
        y -= 20

    c.save()
    pdf_output.seek(0)
    return pdf_output

# === Telegram-логика ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas
    if is_generating_ideas:
        return
    is_generating_ideas = True

    doc = update.message.document
    file = await doc.get_file()
    file_path = f"/tmp/{doc.file_name}"
    await file.download_to_drive(file_path)
    awaiting_caption[update.effective_chat.id] = {"file_path": file_path}

    await update.message.reply_text("Бриф получен! Хочешь добавить сопроводительное сообщение?")
    is_generating_ideas = False

async def collect_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in awaiting_caption:
        await update.message.reply_text("Спасибо! Принял в работу, скоро пришлю идеи в PDF 😊")
        file_path = awaiting_caption.pop(chat_id)["file_path"]
        brief_text = extract_text_from_pdf(file_path) if file_path.endswith(".pdf") else extract_text_from_docx(file_path)
        instructions = update.message.text.strip()
        ideas = await generate_ideas_from_brief(brief_text, instructions)
        brief_context[chat_id] = ideas
        pdf_file = create_pdf(ideas)
        await update.message.reply_document(InputFile(pdf_file, filename="ideas.pdf"))
        keyboard = [[
            InlineKeyboardButton("✅ Принять", callback_data="accept"),
            InlineKeyboardButton("💬 Комментировать", callback_data="comment")
        ]]
        await update.message.reply_text("Что делаем дальше?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if "rewrite" not in context.user_data:
        return await chat_mode(update, context)

    comments_context[chat_id].append(update.message.text)
    await update.message.reply_text("Комментарий принят. Генерирую обновлённый PDF...")
    new_ideas = await regenerate_ideas(brief_context[chat_id], comments_context[chat_id], context.user_data["rewrite"])
    brief_context[chat_id] = new_ideas
    comments_context[chat_id] = []
    context.user_data.pop("rewrite", None)
    pdf_file = create_pdf(new_ideas)
    await update.message.reply_document(InputFile(pdf_file, filename="ideas_updated.pdf"))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()
    if query.data == "accept":
        await query.edit_message_text("Отлично, работа принята ✅")
    elif query.data == "comment":
        keyboard = [[
            InlineKeyboardButton("🛠 Доработать", callback_data="revise"),
            InlineKeyboardButton("♻️ Заново", callback_data="rewrite")
        ]]
        await query.edit_message_text("Как вносим правки?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data in ["revise", "rewrite"]:
        context.user_data["rewrite"] = (query.data == "rewrite")
        await query.edit_message_text("Напиши свои комментарии:")

async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": update.message.text}],
        temperature=0.7,
        max_tokens=800
    )
    await update.message.reply_text(response.choices[0].message.content.strip())

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Готов! Отправь бриф")))
    app.add_handler(CommandHandler("stop", lambda u, c: u.message.reply_text("Бот остановлен")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), collect_text))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
