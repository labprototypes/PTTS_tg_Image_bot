import os
import openai
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fpdf import FPDF
from io import BytesIO

openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

is_generating_ideas = False


# GPT-4o генерация
async def generate_ideas_from_brief(brief_text: str) -> str:
    response = openai.ChatCompletion.create(
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
    global is_generating_ideas
    if is_generating_ideas:
        await update.message.reply_text("Сейчас я генерирую идеи. Подожди немного.")
    else:
        await update.message.reply_text("Привет! Отправь мне бриф в PDF или DOC, и я сгенерирую идеи.")


# Файл-бриф
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_generating_ideas
    if is_generating_ideas:
        await update.message.reply_text("Подожди, я ещё обрабатываю предыдущий бриф.")
        return

    is_generating_ideas = True
    await update.message.reply_text("Бриф получен. Читаю и думаю...")

    document = update.message.document
    file = await document.get_file()
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)

    # ⚠️ Заглушка: вставь сюда нормальную обработку PDF/DOC
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        brief_text = f.read()

    ideas = await generate_ideas_from_brief(brief_text)
    pdf_file = create_pdf(ideas)

    await update.message.reply_document(document=InputFile(pdf_file, filename="ideas.pdf"))
    await update.message.reply_text("Готово! Можем снова болтать 🙂")

    is_generating_ideas = False


# Основной запуск
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.run_polling()


if __name__ == "__main__":
    main()
