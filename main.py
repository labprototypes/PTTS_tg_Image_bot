import os
import openai
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from fpdf import FPDF
from io import BytesIO

# Загрузка API ключей из переменных окружения
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Состояние бота (диалог или генерация идей)
is_generating_ideas = False

# Функция для генерации идей с использованием GPT-4o
def generate_ideas_from_brief(brief_text: str):
    # Используем модель gpt-4o
    response = openai.Completion.create(
        engine="gpt-4o",  # Указание модели gpt-4o
        prompt=f"Based on the brief: {brief_text}\nGenerate 5 creative ideas for a project. Each idea should include:\n1) Name\n2) Intro\n3) Short description\n4) Detailed description\n5) Video script\n6) Why it's a good idea",
        max_tokens=1500,
        temperature=0.7
    )
    return response.choices[0].text.strip()

# Функция для создания PDF
def create_pdf(ideas: str):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="Creative Ideas", ln=True, align="C")
    
    pdf.set_font("Arial", size=12)
    for idea_number, idea in enumerate(ideas.split("\n\n"), start=1):
        pdf.ln(10)
        pdf.set_font("Arial", "B", 14)
        pdf.cell(200, 10, txt=f"Idea {idea_number}: {idea.splitlines()[0]}", ln=True)
        pdf.set_font("Arial", size=12)
        for line in idea.splitlines()[1:]:
            pdf.multi_cell(0, 10, line)
    
    # Сохранение PDF в байтовый поток
    pdf_output = BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return pdf_output

# Функция для обработки команды /start
def start(update: Update, context: CallbackContext):
    global is_generating_ideas
    if not is_generating_ideas:
        update.message.reply_text("Hello! Send me a brief in PDF or DOC format to generate creative ideas.")
    else:
        update.message.reply_text("Please wait, I am generating ideas. Once done, you can continue chatting.")

# Функция для обработки отправленных файлов
def handle_document(update: Update, context: CallbackContext):
    global is_generating_ideas
    if not is_generating_ideas:
        is_generating_ideas = True
        file = update.message.document.get_file()
        file.download("brief.pdf")

        # Обработка PDF файла и извлечение текста (можно использовать библиотеки для обработки PDF, как PyPDF2 или pdfminer)
        brief_text = "Extracted text from PDF brief (or DOC)"
        
        ideas = generate_ideas_from_brief(brief_text)
        pdf_file = create_pdf(ideas)
        
        # Отправка PDF в чат
        update.message.reply_document(document=InputFile(pdf_file, filename="creative_ideas.pdf"))
        
        is_generating_ideas = False
        update.message.reply_text("Here are the generated ideas! You can continue chatting with me.")
    else:
        update.message.reply_text("Please wait, I am still processing the last request.")

# Настройка и запуск бота
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.document.mime_type("application/pdf") | Filters.document.mime_type("application/msword"), handle_document))
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
