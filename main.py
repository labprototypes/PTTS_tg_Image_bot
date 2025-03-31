import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from openai import OpenAI

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Tokens from Render Environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с GPT-4 и DALL-E 3. Напиши текст — отвечу. Напиши 'картинка: ...' — нарисую!")

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.lower().startswith("картинка:"):
        await update.message.reply_text("🖼 Генерирую картинку...")
        prompt = text[len("картинка:"):].strip()

        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1
            )
            image_url = response.data[0].url
            await update.message.reply_photo(photo=image_url, caption=prompt)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при генерации картинки:\n{e}")
    else:
        try:
            chat_response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": text}]
            )
            answer = chat_response.choices[0].message.content
            await update.message.reply_text(answer)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка GPT:\n{e}")

# Main
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()
