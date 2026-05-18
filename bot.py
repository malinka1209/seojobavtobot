import os
import logging
import requests
import re
import random
import asyncio
import traceback
import json
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== ПЕРЕМЕННЫЕ ИЗ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8017102780"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "seoavto")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "lynxfix")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден!")
    exit(1)

LOG_FOLDER = "bot_logs"
os.makedirs(LOG_FOLDER, exist_ok=True)


# ========== ГЕНЕРАТОР ==========
class SmartReviewGenerator:
    def generate(self, company, instruction_text):
        templates = [
            f"Обратился в {company} за услугой. Всё сделали качественно и быстро. Рекомендую!",
            f"Хороший сервис! Заказывал в {company} - доволен результатом. Буду обращаться еще.",
            f"{company} - отличное место. Работу выполнили на совесть. Спасибо!",
        ]
        review = random.choice(templates)
        return review, len(review)


# ========== ПАРСИНГ ==========
def parse_instruction(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    full_text = soup.get_text()

    company = "Компания"
    for line in full_text.split('\n')[:20]:
        line = line.strip()
        if 3 < len(line) < 80:
            skip_words = ['стоимость', 'важно', 'запрещается', 'шаг', 'задание', 'инструкция']
            if not any(s in line.lower() for s in skip_words):
                company = line.split(',')[0].split(' - ')[0].strip()
                if len(company) > 3:
                    break

    address = "Санкт-Петербург"
    addr_match = re.search(r'(Санкт-Петербург[^,\n]+|Москва[^,\n]+|ул\.[^,\n]+|наб\.[^,\n]+)', full_text)
    if addr_match:
        address = addr_match.group(1)

    return {
        'company': company,
        'address': address,
        'instruction_text': full_text[:4000]
    }


# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👤 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])
    await update.message.reply_text(
        f"🤖 SEO Job Bot\n\nОтправь ссылку на инструкцию jobseo\n💰 До 300₽ за отзыв\n\n📢 @{CHANNEL_USERNAME}",
        reply_markup=keyboard
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("https://instructions.jobseo.ru/"):
        await update.message.reply_text("❌ Неверная ссылка!")
        return

    msg = await update.message.reply_text("🔍 Обрабатываю...")

    try:
        data = parse_instruction(url)
        generator = SmartReviewGenerator()
        review, length = generator.generate(data['company'], data['instruction_text'])
        maps_url = f"https://yandex.ru/maps/?text={urllib.parse.quote(data['address'])}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗺 Карты", url=maps_url)],
            [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
            [InlineKeyboardButton("🔄 Другой", callback_data="new_review")]
        ])

        context.user_data['last_review'] = review
        context.user_data['last_url'] = url
        context.user_data['last_instruction'] = data['instruction_text']
        context.user_data['last_company'] = data['company']
        context.user_data['last_address'] = data['address']

        await msg.edit_text(
            f"✅ {data['company']}\n📍 {data['address']}\n\n📝 {review}\n\n1️⃣ Карты → 2️⃣ Отзывы → 3️⃣ 5★ → 4️⃣ Вставить → 5️⃣ Скриншот → @jobseo_bot\n\n💰 Получи оплату!",
            reply_markup=keyboard
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка\n\n👨‍💻 @{SUPPORT_USERNAME}")


async def copy_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review = context.user_data.get('last_review', '')
    await query.message.reply_text(f"📋 Скопируй:\n\n{review}")


async def new_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Генерирую...")

    generator = SmartReviewGenerator()
    review, length = generator.generate(
        context.user_data.get('last_company', 'Компания'),
        context.user_data.get('last_instruction', '')
    )
    context.user_data['last_review'] = review

    await query.edit_message_text(f"🆕 Новый отзыв:\n\n{review}")


# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(copy_review_callback, pattern="copy_review"))
    app.add_handler(CallbackQueryHandler(new_review_callback, pattern="new_review"))

    print("✅ БОТ ЗАПУЩЕН!")
    app.run_polling()


if __name__ == "__main__":
    main()