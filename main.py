import openai
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

openai.api_key = OPENAI_API_KEY

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с GPT-4 и DALL·E 3. Напиши текст — отвечу. Напиши 'картинка: ...' — нарисую!")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text.lower().startswith("картинка:"):
        prompt = text.split("картинка:", 1)[1].strip()
        await update.message.reply_text("🎨 Генерирую картинку...")
        try:
            response = openai.Image.create(
                prompt=prompt,
                model="dall-e-3",
                size="1024x1024",
                n=1
            )
            image_url = response['data'][0]['url']
            await update.message.reply_photo(photo=image_url)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при генерации картинки: {e}")
    else:
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": text}]
            )
            reply = completion.choices[0].message["content"]
            await update.message.reply_text(reply)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при ответе: {e}")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
