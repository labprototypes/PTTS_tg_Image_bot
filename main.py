import os
import logging
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI
from dotenv import load_dotenv
import pdfplumber
import docx
from fpdf import FPDF
import cairosvg

# Загрузка переменных окружения
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# Логотип и шрифт
LOGO_PATH = "long logo h100.svg"
FONT_PATH = "TT Travels Next Trial Bold.ttf"

logging.basicConfig(level=logging.INFO)

# Обработка команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я могу:\n"
        "- Ответить на любой вопрос\n"
        "- Сгенерировать изображение (начни с 'Картинка: ...')\n"
        "- Принять бриф через /brief и выдать 5 идей в PDF"
    )

# Обработка текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.lower().startswith("картинка:"):
        prompt = text.split("картинка:", 1)[1].strip()
        await update.message.reply_text("Генерирую изображение...")
        image = client.images.generate(prompt=prompt, n=1, size="1024x1024")
        image_url = image.data[0].url
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=image_url)
    else:
        await update.message.reply_chat_action(action="typing")
        chat_completion = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": text}]
        )
        await update.message.reply_text(chat_completion.choices[0].message.content)

# Обработка команды /brief
async def brief_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Пожалуйста, отправьте файл брифа сразу после команды /brief.")
        return

    await update.message.reply_text("📨 Загружаю файл...")
    file = await context.bot.get_file(update.message.document.file_id)
    file_path = f"brief.{file.file_path.split('.')[-1]}"
    await file.download_to_drive(file_path)

    await update.message.reply_text("📄 Извлекаю текст из брифа...")
    text = extract_text(file_path)
    if not text:
        await update.message.reply_text("❌ Не удалось извлечь текст из файла.")
        return

    await update.message.reply_text("🧠 Отправляю бриф в GPT для генерации идей...")
    gpt_response = await generate_ideas(text)

    await update.message.reply_text("📄 Собираю PDF с результатом...")
    pdf_path = "ideas_output.pdf"
    try:
        generate_pdf(gpt_response, pdf_path)
        await update.message.reply_document(InputFile(pdf_path))
    except Exception as e:
        await update.message.reply_text(f"Ошибка генерации PDF:\n{str(e)}")

# Генерация идей через GPT
async def generate_ideas(text):
    prompt = (
        f"Ты креативщик. Придумай 5 уникальных идей по следующему брифу. "
        f"Формат каждой идеи:\n\n"
        f"1) Название идеи\n"
        f"2) Вводная часть\n"
        f"3) Короткое описание идеи\n"
        f"4) Полное описание идеи\n"
        f"5) Реализация идеи\n"
        f"   5.1) Если это видеоролик – предложи сценарий\n"
        f"   5.2) Если это 360-кампания – предложи раскладку по каналам\n"
        f"   5.3) Если это креативный сиддинг – предложи механику\n"
        f"   5.4) Если это ивент – предложи реализацию\n\n"
        f"Бриф:\n{text}"
    )
    completion = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content

# Извлечение текста
def extract_text(path):
    ext = path.split(".")[-1].lower()
    try:
        if ext == "pdf":
            with pdfplumber.open(path) as pdf:
                return "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
        elif ext in ["docx", "doc"]:
            doc = docx.Document(path)
            return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print("Ошибка при извлечении текста:", e)
    return None

# Генерация PDF
def generate_pdf(text, output_path):
    pdf = FPDF()
    pdf.add_page()

    # Вставляем логотип
    logo_temp = "logo_temp.png"
    cairosvg.svg2png(url=LOGO_PATH, write_to=logo_temp, output_width=200)
    pdf.image(logo_temp, x=10, y=10, w=40)

    # Подготовка шрифта
    pdf.add_font("CustomFont", "", FONT_PATH, uni=True)
    pdf.set_font("CustomFont", size=12)
    pdf.ln(50)  # отступ после логотипа
    pdf.multi_cell(0, 10, txt=text, align="L")
    pdf.output(output_path)

# Запуск
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", brief_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, brief_command))

    print("Бот запущен...")
    app.run_polling()
