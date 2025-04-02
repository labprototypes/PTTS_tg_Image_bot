import os
import sys
import openai
import fitz  # PyMuPDF
import re
from docx import Document
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from io import BytesIO
import atexit
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from textwrap import wrap

# Защита от повторного запуска
lock_file = "/tmp/bot.lock"
if os.path.exists(lock_file):
    print("Бот уже запущен.")
    sys.exit()
with open(lock_file, "w") as f:
    f.write("running")
atexit.register(lambda: os.remove(lock_file))

# Переменные окружения
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

client = openai.AsyncOpenAI(api_key=openai.api_key)

# Глобальные флаги
is_generating_ideas = False
is_active = True

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join(page.get_text() for page in doc)

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

async def generate_ideas_from_brief(brief_text: str, instructions: str = "") -> str:
    prompt = (
        "Ты сильный креативный директор. На основе брифа сгенерируй 5 насыщенных креативных идей.\n"
        "Формат:\n"
        "Идея 1: Название\n"
        "Интро: минимум 2 абзаца\n"
        "Кратко: 1 фраза\n"
        "Подробно: минимум 2 абзаца\n"
        "Сценарий: минимум 5 подпунктов\n"
        "Почему идея хорошая: минимум 3 подпункта\n\n"
        "Не используй * и #. Пиши чисто и читаемо.\n\n"
        f"Инструкция: {instructions}\n\nБриф:\n{brief_text}"
    )
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.95,
        max_tokens=4000,
    )
    return re.sub(r"[*#]+", "", response.choices[0].message.content.strip())

async def regenerate_ideas(original: str, comments: list[str], rewrite_all: bool) -> str:
    joined_comments = "\n".join(f"- {c}" for c in comments)
    instruction = "Полностью перепиши идеи" if rewrite_all else "Улучши текущие идеи"
    prompt = (
        f"{instruction} с учётом комментариев ниже.\n\n"
        f"Оригинальные идеи:\n{original}\n\nКомментарии:\n{joined_comments}\n\n"
        "Формат:\n"
        "Идея 1: Название\nИнтро: 2 абзаца\nКратко: 1 фраза\nПодробно: 2 абзаца\n"
        "Сценарий: 5 пунктов\nПочему идея хорошая: 3 пункта\n\nБез markdown."
    )
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.95,
        max_tokens=4000,
    )
   
    return re.sub(r"[*#]+", "", response.choices[0].message.content.strip())
def create_pdf(ideas: str) -> BytesIO:
    pdf_output = BytesIO()
    c = canvas.Canvas(pdf_output, pagesize=letter)
    width, height = letter

    # Шрифт
    font_path = "TT_Norms_Pro_Trial_Expanded_Medium.ttf"
    pdfmetrics.registerFont(TTFont('CustomFont', font_path))

    margin_x = 50
    max_width = width - 2 * margin_x - 20  # увеличенный правый отступ
    y = height - 50

    c.setFont("CustomFont", 12)
    heading_size = 16
    subheading_size = 13
    font_size = 11.5
    line_height = 15

    ideas_list = re.split(r"(?=\n?Идея \d+:)", ideas.strip())
    for idx, idea in enumerate(ideas_list):
        if idx > 0:
            y -= 40  # большой отступ между идеями

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
                                c.setFont("CustomFont", font_size)
                                y = height - 50
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

from collections import defaultdict

brief_context = {}
comments_context = defaultdict(list)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = True
    await update.message.reply_text("Привет! Отправь мне бриф (PDF/DOCX) с сопроводительным сообщением. Я сгенерирую идеи.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = False
    await update.message.reply_text("Бот остановлен. Чтобы начать заново — напиши /start.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas, is_active
    if not is_active or is_generating_ideas:
        return

    is_generating_ideas = True
    caption = update.message.caption or ""
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

    full_text = caption + "\n\n" + brief_text if caption else brief_text
    ideas = await generate_ideas_from_brief(full_text)
    brief_context[update.effective_chat.id] = ideas

    pdf_file = create_pdf(ideas)
    await update.message.reply_document(InputFile(pdf_file, filename="ideas.pdf"))

    keyboard = [
        [InlineKeyboardButton("✅ Принять", callback_data="accept"),
         InlineKeyboardButton("💬 Комментировать", callback_data="comment")]
    ]
    await update.message.reply_text("Что делаем дальше?", reply_markup=InlineKeyboardMarkup(keyboard))
    is_generating_ideas = False

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "accept":
        await query.edit_message_text("Отлично! Работа принята ✅")
        return

    if query.data == "comment":
        keyboard = [
            [InlineKeyboardButton("🛠 Доработать текущие", callback_data="revise"),
             InlineKeyboardButton("♻️ Переделать заново", callback_data="rewrite")]
        ]
        await query.edit_message_text("Как будем вносить правки?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data in ["revise", "rewrite"]:
        context.user_data["rewrite"] = query.data == "rewrite"
        await query.edit_message_text("Напиши свои комментарии к идеям. Я перегенерирую PDF после этого.")

async def collect_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if "rewrite" not in context.user_data:
        return await chat_mode(update, context)

    comments_context[chat_id].append(update.message.text)
    await update.message.reply_text("Комментарий получен. Генерирую новый PDF...")

    old_ideas = brief_context.get(chat_id, "")
    comments = comments_context[chat_id]
    rewrite_all = context.user_data.get("rewrite", False)

    new_ideas = await regenerate_ideas(old_ideas, comments, rewrite_all)
    brief_context[chat_id] = new_ideas
    comments_context[chat_id] = []
    context.user_data.pop("rewrite", None)

    pdf_file = create_pdf(new_ideas)
    await update.message.reply_document(InputFile(pdf_file, filename="ideas_updated.pdf"))
    await update.message.reply_text("Готово! Новая версия идей сформирована ✨")

async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_active or is_generating_ideas:
        return

    user_msg = update.message.text
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты дружелюбный ассистент, говоришь с пользователем в свободной форме."},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.7,
        max_tokens=800,
    )
    await update.message.reply_text(response.choices[0].message.content.strip())

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), collect_comment))
    app.run_polling()

if __name__ == "__main__":
    main()
