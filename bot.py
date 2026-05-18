import os
import logging
import requests
import re
import random
import asyncio
import traceback
import json
import urllib.parse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env ==========
from dotenv import load_dotenv

load_dotenv()

# ========== ПЕРЕМЕННЫЕ (БЕЗОПАСНО) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8017102780"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "seoavto")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "lynxfix")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден в .env файле!")
    exit(1)

# Файлы для хранения данных
USERS_STATS_FILE = "users_stats.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
SETTINGS_FILE = "settings.json"
LOG_FOLDER = "bot_logs"

# Создаем папки
os.makedirs(LOG_FOLDER, exist_ok=True)


# ========== НАСТРОЙКИ ==========
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"min_review_length": 100, "max_review_length": 400}


def save_settings(settings):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# Загружаем настройки
settings = load_settings()


# ========== ЗАГРУЗКА/СОХРАНЕНИЕ ДАННЫХ ==========
def load_stats():
    if os.path.exists(USERS_STATS_FILE):
        with open(USERS_STATS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_stats(stats):
    with open(USERS_STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)


# ========== ТАРИФЫ ПОДПИСОК ==========
SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Бесплатный",
        "emoji": "🆓",
        "daily_limit": 2,
        "price": 0,
        "days": 0
    },
    "basic": {
        "name": "Базовый",
        "emoji": "📦",
        "daily_limit": 20,
        "price": 199,
        "days": 30
    },
    "pro": {
        "name": "Про",
        "emoji": "💎",
        "daily_limit": 100,
        "price": 499,
        "days": 30
    },
    "unlimited": {
        "name": "Безлимит",
        "emoji": "👑",
        "daily_limit": 999999,
        "price": 999,
        "days": 30
    }
}


# ========== СИСТЕМА УРОВНЕЙ И ДОСТИЖЕНИЙ ==========
class RankingSystem:
    LEVELS = {
        0: {"name": "🟢 Новичок", "min_reviews": 0, "emoji": "🌱"},
        1: {"name": "🔵 Практикант", "min_reviews": 5, "emoji": "📝"},
        2: {"name": "🔷 Мастер", "min_reviews": 15, "emoji": "⚙️"},
        3: {"name": "🟣 Профи", "min_reviews": 40, "emoji": "💎"},
        4: {"name": "🟡 Эксперт", "min_reviews": 90, "emoji": "👑"},
        5: {"name": "🔴 Гуру", "min_reviews": 190, "emoji": "🏆"},
    }

    ACHIEVEMENTS = {
        "first_review": {"name": "🎯 Первый отзыв", "reviews_needed": 1},
        "five_reviews": {"name": "⭐ 5 отзывов", "reviews_needed": 5},
        "fifteen_reviews": {"name": "🏅 15 отзывов", "reviews_needed": 15},
        "forty_reviews": {"name": "💎 40 отзывов", "reviews_needed": 40},
        "hundred_reviews": {"name": "🔥 100 отзывов", "reviews_needed": 100},
    }

    @classmethod
    def get_level(cls, review_count):
        for level_id, level_data in sorted(cls.LEVELS.items(), reverse=True):
            if review_count >= level_data["min_reviews"]:
                return level_id, level_data
        return 0, cls.LEVELS[0]

    @classmethod
    def check_new_achievements(cls, user_id, old_count, new_count, stats):
        new_achievements = []
        for ach_id, ach_data in cls.ACHIEVEMENTS.items():
            if ach_id not in stats.get("achievements", []):
                if new_count >= ach_data["reviews_needed"]:
                    new_achievements.append(ach_data)
        return new_achievements


# ========== УМНЫЙ ГЕНЕРАТОР ОТЗЫВОВ ==========
class ReviewGenerator:

    def __init__(self):
        self.min_length = settings.get("min_review_length", 100)
        self.max_length = settings.get("max_review_length", 400)

    def _ensure_length(self, review):
        """Подгоняет отзыв под нужную длину"""
        if len(review) < self.min_length:
            # Добавляем фразы
            add_phrases = [" Очень доволен результатом.", " Спасибо за работу!", " Всем рекомендую!",
                           " Буду обращаться еще."]
            while len(review) < self.min_length and add_phrases:
                review += random.choice(add_phrases)
        elif len(review) > self.max_length:
            review = review[:self.max_length - 3] + "..."
        return review

    def generate(self, company, instruction_text):
        text_lower = instruction_text.lower()

        if 'химчистк' in text_lower or 'чистк' in text_lower:
            review = self._cleaning_review(company)
        elif 'авто' in text_lower or 'сервис' in text_lower or 'vag' in text_lower:
            review = self._auto_review(company)
        elif 'мебель' in text_lower or 'кухн' in text_lower or 'шкаф' in text_lower:
            review = self._furniture_review(company)
        elif 'кафе' in text_lower or 'ресторан' in text_lower:
            review = self._cafe_review(company)
        else:
            review = self._default_review(company)

        return self._ensure_length(review), len(review)

    def _cleaning_review(self, company):
        templates = [
            f"""Обратился в {company} за химчисткой. Очень переживал, что вещи испортят, но всё прошло отлично. Пятна вывели полностью, пуховик после чистки как новый. Отдельное спасибо за бережное отношение к вещам. Цены адекватные, сделали за 2 дня. Обязательно буду обращаться еще и друзьям посоветую!""",
            f"""Долго искал где можно качественно почистить вещи. По совету знакомых обратился в {company}. Сдавал куртку и обувь — всё сделали на отлично. Вещи принесли аккуратно упакованные, без запаха химии. Рекомендую {company} всем, кто ценит качество!""",
        ]
        return random.choice(templates)

    def _auto_review(self, company):
        templates = [
            f"""Обратился в {company} на обслуживание. Мастер всё подробно объяснил, показал что нужно заменить. Цены назвали до начала работ, никаких скрытых доплат. Всё сделали качественно и в срок. Машина после ремонта поехала заметно лучше. Буду обращаться еще!""",
            f"""Долго выбирал где обслуживать свой автомобиль. Друзья посоветовали {company}. Очень доволен результатом. Специалисты знают своё дело, работают аккуратно. Рекомендую!"""
        ]
        return random.choice(templates)

    def _furniture_review(self, company):
        templates = [
            f"""Заказывал мебель в {company}. Всё сделали качественно, в срок. Мастер приехал, всё замерил, проконсультировал. Результатом очень доволен, мебель отлично вписалась в интерьер. Рекомендую!""",
            f"""Обратился в {company} за кухней на заказ. Сделали быстро и качественно. Цены адекватные, сборка профессиональная. Спасибо команде {company}!"""
        ]
        return random.choice(templates)

    def _cafe_review(self, company):
        templates = [
            f"""Отличное место! Был в {company} с друзьями. Вкусно, уютно, обслуживание на высоте. Порции большие, цены приятные. Обязательно вернусь еще!""",
            f"""Обедал в {company}. Готовят вкусно, подача быстрая. Рекомендую!"""
        ]
        return random.choice(templates)

    def _default_review(self, company):
        templates = [
            f"""Обратился в {company} за услугой. Всё сделали качественно и быстро. Рекомендую!""",
            f"""Хороший сервис! Заказывал в {company}. Результатом доволен. Спасибо!"""
        ]
        return random.choice(templates)


# ========== ПАРСИНГ ИНСТРУКЦИИ ==========
def parse_instruction(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    full_text = soup.get_text()

    if 'яндекс.карт' not in full_text.lower():
        raise Exception("❌ Это задание НЕ с Яндекс.Картами!")

    company = "Компания"
    for line in full_text.split('\n')[:20]:
        line = line.strip()
        if 3 < len(line) < 80:
            skip_words = ['стоимость', 'важно', 'запрещается', 'шаг', 'задание', 'инструкция']
            if not any(s in line.lower() for s in skip_words):
                company = line.split(',')[0].split(' - ')[0].strip()
                if len(company) > 3:
                    break

    address = None
    addr_patterns = [
        r'(?:адрес[:\s]*)([^.\n]+)',
        r'(?:наб\.[^,\n]+)',
        r'(?:ул\.[^,\n]+)',
        r'(?:площадь[^,\n]+)',
    ]

    for pattern in addr_patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            address = match.group(1).strip() if ':' in pattern else match.group(0).strip()
            address = address[:100]
            break

    if not address:
        address = "не указан"

    return {
        'company': company,
        'address': address,
        'instruction_text': full_text[:5000]
    }


def make_maps_url(address):
    if not address or address == "не указан":
        return "https://yandex.ru/maps/"
    encoded = urllib.parse.quote(address)
    return f"https://yandex.ru/maps/?text={encoded}"


# ========== ПРОВЕРКА ЛИМИТОВ ПОДПИСКИ ==========
def check_user_limit(user_id):
    subscriptions = load_subscriptions()
    stats = load_stats()

    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    sub = subscriptions.get(user_id_str, {})
    plan = sub.get("plan", "free")
    expires = sub.get("expires")

    if expires and datetime.now() > datetime.fromisoformat(expires):
        plan = "free"
        subscriptions[user_id_str] = {"plan": "free", "expires": None}
        save_subscriptions(subscriptions)

    daily_limit = SUBSCRIPTION_PLANS[plan]["daily_limit"]

    if user_id_str not in stats:
        stats[user_id_str] = {"daily_usage": {}}

    used_today = stats[user_id_str].get("daily_usage", {}).get(today, 0)

    if used_today >= daily_limit:
        return False, daily_limit, used_today, plan

    return True, daily_limit, used_today, plan


def increment_user_usage(user_id):
    stats = load_stats()
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    if user_id_str not in stats:
        stats[user_id_str] = {"daily_usage": {}}

    if "daily_usage" not in stats[user_id_str]:
        stats[user_id_str]["daily_usage"] = {}

    stats[user_id_str]["daily_usage"][today] = stats[user_id_str]["daily_usage"].get(today, 0) + 1
    save_stats(stats)


# ========== АДМИН-КОМАНДЫ ==========
async def admin_give_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Использование: /give_sub <user_id> <plan> <days>\n\n"
            "Пример: /give_sub 123456789 pro 30\n\n"
            "Планы: free, basic, pro, unlimited"
        )
        return

    try:
        user_id = int(args[0])
        plan = args[1].lower()
        days = int(args[2])

        if plan not in SUBSCRIPTION_PLANS:
            await update.message.reply_text(f"❌ План {plan} не существует")
            return

        subscriptions = load_subscriptions()
        expires = (datetime.now() + timedelta(days=days)).isoformat()

        subscriptions[str(user_id)] = {
            "plan": plan,
            "expires": expires,
            "given_by": ADMIN_ID,
            "given_at": datetime.now().isoformat()
        }

        save_subscriptions(subscriptions)

        plan_name = SUBSCRIPTION_PLANS[plan]["name"]
        await update.message.reply_text(f"✅ Подписка {plan_name} выдана пользователю {user_id} на {days} дней")

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 Вам выдана подписка {plan_name} на {days} дней!",
                parse_mode='HTML'
            )
        except:
            pass

    except ValueError:
        await update.message.reply_text("❌ Неверный формат")


async def admin_remove_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Использование: /remove_sub <user_id>")
        return

    try:
        user_id = int(args[0])
        subscriptions = load_subscriptions()

        if str(user_id) in subscriptions:
            del subscriptions[str(user_id)]
            save_subscriptions(subscriptions)
            await update.message.reply_text(f"✅ Подписка у пользователя {user_id} удалена")
        else:
            await update.message.reply_text(f"❌ У пользователя {user_id} нет подписки")

    except ValueError:
        await update.message.reply_text("❌ Неверный формат")


async def admin_set_min_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить минимальную длину отзыва (только админ)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            f"❌ Использование: /set_min_length <символы>\n\n"
            f"Пример: /set_min_length 150\n\n"
            f"Текущая минимальная длина: {settings.get('min_review_length', 100)} символов"
        )
        return

    try:
        new_length = int(args[0])
        if new_length < 50:
            await update.message.reply_text("❌ Минимальная длина не может быть меньше 50 символов")
            return
        if new_length > 500:
            await update.message.reply_text("❌ Минимальная длина не может быть больше 500 символов")
            return

        settings["min_review_length"] = new_length
        save_settings(settings)

        await update.message.reply_text(f"✅ Минимальная длина отзыва установлена: {new_length} символов")

    except ValueError:
        await update.message.reply_text("❌ Введите число")


async def admin_set_max_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить максимальную длину отзыва (только админ)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            f"❌ Использование: /set_max_length <символы>\n\n"
            f"Пример: /set_max_length 400\n\n"
            f"Текущая максимальная длина: {settings.get('max_review_length', 400)} символов"
        )
        return

    try:
        new_length = int(args[0])
        if new_length < 100:
            await update.message.reply_text("❌ Максимальная длина не может быть меньше 100 символов")
            return
        if new_length > 1000:
            await update.message.reply_text("❌ Максимальная длина не может быть больше 1000 символов")
            return

        settings["max_review_length"] = new_length
        save_settings(settings)

        await update.message.reply_text(f"✅ Максимальная длина отзыва установлена: {new_length} символов")

    except ValueError:
        await update.message.reply_text("❌ Введите число")


async def admin_show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие настройки (только админ)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    await update.message.reply_text(
        f"📊 <b>Текущие настройки бота</b>\n\n"
        f"📏 Мин. длина отзыва: {settings.get('min_review_length', 100)} симв.\n"
        f"📏 Макс. длина отзыва: {settings.get('max_review_length', 400)} симв.\n"
        f"👑 Админ ID: {ADMIN_ID}\n"
        f"📢 Канал: @{CHANNEL_USERNAME}\n"
        f"👤 Поддержка: @{SUPPORT_USERNAME}",
        parse_mode='HTML'
    )


# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    stats = load_stats()
    if user_id not in stats:
        stats[user_id] = {
            "name": user.first_name,
            "reviews_count": 0,
            "achievements": [],
            "first_seen": datetime.now().isoformat(),
            "daily_usage": {}
        }
        save_stats(stats)

    review_count = stats[user_id]["reviews_count"]
    level_id, level_data = RankingSystem.get_level(review_count)
    can_use, limit, used, plan = check_user_limit(user.id)
    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Профиль", callback_data="my_profile")],
        [InlineKeyboardButton("📊 Подписка", callback_data="my_subscription")],
        [InlineKeyboardButton("📢 Канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👤 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])

    await update.message.reply_text(
        f"🤖 <b>SEO Job Bot</b>\n\n"
        f"👋 Привет, {user.first_name}!\n"
        f"{level_data['emoji']} <b>Уровень:</b> {level_data['name']}\n"
        f"📊 <b>Отзывов:</b> {review_count}\n"
        f"{plan_emoji} <b>Подписка:</b> {plan_name}\n"
        f"📅 <b>Лимит:</b> {used}/{limit}\n\n"
        f"📌 Отправь ссылку на инструкцию\n"
        f"💰 До 300₽ за отзыв!\n\n"
        f"📢 @{CHANNEL_USERNAME}",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    user_id = str(user.id)

    stats = load_stats()
    if user_id not in stats:
        stats[user_id] = {"reviews_count": 0, "achievements": [], "first_seen": datetime.now().isoformat()}
        save_stats(stats)

    review_count = stats[user_id]["reviews_count"]
    level_id, level_data = RankingSystem.get_level(review_count)
    achievements = stats[user_id].get("achievements", [])

    achievements_text = ""
    for ach_id, ach_data in RankingSystem.ACHIEVEMENTS.items():
        if ach_id in achievements:
            achievements_text += f"✅ {ach_data['name']}\n"
        else:
            achievements_text += f"⬜ {ach_data['name']}\n"

    text = f"🏆 <b>ПРОФИЛЬ</b>\n\n"
    text += f"👤 {user.first_name}\n"
    text += f"{level_data['emoji']} {level_data['name']}\n"
    text += f"📊 Отзывов: {review_count}\n\n"
    text += f"<b>🏅 ДОСТИЖЕНИЯ:</b>\n{achievements_text}"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]])
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)


async def my_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    subscriptions = load_subscriptions()

    sub = subscriptions.get(str(user_id), {"plan": "free"})
    plan = sub.get("plan", "free")
    expires = sub.get("expires")

    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]
    daily_limit = SUBSCRIPTION_PLANS[plan]["daily_limit"]

    can_use, limit, used, _ = check_user_limit(user_id)

    text = f"{plan_emoji} <b>Подписка</b>\n\n"
    text += f"📦 Тариф: {plan_name}\n"
    text += f"📅 Лимит: {daily_limit} отзывов/день\n"
    text += f"📊 Сегодня: {used}/{daily_limit}\n"

    if expires:
        text += f"⏰ Действует до: {expires[:10]}\n"
    else:
        text += f"\n🆓 Бесплатный тариф (2 отзыва в день)\n"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]])
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    user_id = str(user.id)

    stats = load_stats()
    review_count = stats.get(user_id, {}).get("reviews_count", 0)
    level_id, level_data = RankingSystem.get_level(review_count)
    can_use, limit, used, plan = check_user_limit(user.id)
    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Профиль", callback_data="my_profile")],
        [InlineKeyboardButton("📊 Подписка", callback_data="my_subscription")],
        [InlineKeyboardButton("📢 Канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👤 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])

    await query.edit_message_text(
        f"🤖 <b>SEO Job Bot</b>\n\n"
        f"👋 {user.first_name}\n"
        f"{level_data['emoji']} {level_data['name']}\n"
        f"📊 Отзывов: {review_count}\n"
        f"{plan_emoji} {plan_name} ({used}/{limit})\n\n"
        f"📌 Отправь ссылку",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.effective_user
    user_id = str(user.id)

    if not url.startswith("https://instructions.jobseo.ru/"):
        await update.message.reply_text("❌ Неверная ссылка!")
        return

    can_use, limit, used, plan = check_user_limit(user.id)

    if not can_use:
        plan_name = SUBSCRIPTION_PLANS[plan]["name"]
        await update.message.reply_text(
            f"❌ <b>Лимит исчерпан!</b>\n\n"
            f"Тариф: {plan_name}\n"
            f"Лимит: {limit} отзывов/день\n"
            f"Использовано: {used}/{limit}\n\n"
            f"👤 @{SUPPORT_USERNAME}",
            parse_mode='HTML'
        )
        return

    status_msg = await update.message.reply_text("🔍 Анализирую...")

    try:
        data = parse_instruction(url)
        generator = ReviewGenerator()
        review, review_length = generator.generate(data['company'], data['instruction_text'])
        maps_url = make_maps_url(data['address'])

        stats = load_stats()
        if user_id not in stats:
            stats[user_id] = {"reviews_count": 0, "achievements": [], "first_seen": datetime.now().isoformat(),
                              "daily_usage": {}}

        old_count = stats[user_id].get("reviews_count", 0)
        new_count = old_count + 1
        stats[user_id]["reviews_count"] = new_count

        today = datetime.now().strftime("%Y-%m-%d")
        if "daily_usage" not in stats[user_id]:
            stats[user_id]["daily_usage"] = {}
        stats[user_id]["daily_usage"][today] = stats[user_id]["daily_usage"].get(today, 0) + 1

        new_achievements = RankingSystem.check_new_achievements(user_id, old_count, new_count, stats[user_id])
        for ach in new_achievements:
            if "achievements" not in stats[user_id]:
                stats[user_id]["achievements"] = []
            stats[user_id]["achievements"].append(ach["name"])

        save_stats(stats)
        increment_user_usage(user.id)

        level_id, level_data = RankingSystem.get_level(new_count)

        level_up = ""
        if level_id > 0 and old_count < RankingSystem.LEVELS[level_id]["min_reviews"] <= new_count:
            level_up = f"\n\n🎉 <b>НОВЫЙ УРОВЕНЬ: {level_data['name']}</b>"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗺 Карты", url=maps_url)],
            [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
            [InlineKeyboardButton("🔄 Другой", callback_data="new_review")]
        ])

        context.user_data['last_review'] = review
        context.user_data['last_instruction'] = data['instruction_text']
        context.user_data['last_company'] = data['company']
        context.user_data['last_address'] = data['address']

        await status_msg.edit_text(
            f"✅ <b>{data['company']}</b>\n📍 {data['address']}\n\n"
            f"📝 <b>Отзыв ({review_length} симв.):</b>\n<code>{review}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{level_data['emoji']} Уровень: {level_data['name']}\n"
            f"📊 Всего: {new_count} отзывов{level_up}\n"
            f"📅 Сегодня: {used + 1}/{limit}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"1️⃣ Карты → 2️⃣ Отзывы → 3️⃣ 5★ → 4️⃣ Вставить\n"
            f"5️⃣ Отправить → 6️⃣ Скриншот → 7️⃣ @jobseo_bot\n\n"
            f"💰 <b>Оплата после проверки!</b>",
            parse_mode='HTML',
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}\n\n👨‍💻 @{SUPPORT_USERNAME}")


async def copy_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review = context.user_data.get('last_review', '')
    if review:
        await query.message.reply_text(f"📋 <b>Копируй:</b>\n\n<code>{review}</code>", parse_mode='HTML')
    else:
        await query.answer("❌ Ошибка", show_alert=True)


async def new_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Генерирую...")

    instruction = context.user_data.get('last_instruction')
    company = context.user_data.get('last_company')
    address = context.user_data.get('last_address', '')

    if not instruction:
        await query.edit_message_text("❌ Ошибка")
        return

    generator = ReviewGenerator()
    new_review, new_length = generator.generate(company, instruction)
    context.user_data['last_review'] = new_review

    maps_url = make_maps_url(address)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
        [InlineKeyboardButton("🔄 Еще", callback_data="new_review")],
        [InlineKeyboardButton("🗺 Карты", url=maps_url)]
    ])

    await query.edit_message_text(f"🆕 <b>Новый отзыв ({new_length} симв.):</b>\n\n<code>{new_review}</code>",
                                  parse_mode='HTML', reply_markup=keyboard)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"🆔 Твой ID: `{user_id}`", parse_mode='Markdown')


# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Админ-команды
    app.add_handler(CommandHandler("give_sub", admin_give_subscription))
    app.add_handler(CommandHandler("remove_sub", admin_remove_subscription))
    app.add_handler(CommandHandler("set_min_length", admin_set_min_length))
    app.add_handler(CommandHandler("set_max_length", admin_set_max_length))
    app.add_handler(CommandHandler("settings", admin_show_settings))

    # Пользовательские команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback-обработчики
    app.add_handler(CallbackQueryHandler(my_profile, pattern="^my_profile$"))
    app.add_handler(CallbackQueryHandler(my_subscription, pattern="^my_subscription$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(copy_review_callback, pattern="^copy_review$"))
    app.add_handler(CallbackQueryHandler(new_review_callback, pattern="^new_review$"))

    print("=" * 50)
    print("✅ БОТ ЗАПУЩЕН!")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"📏 Мин. длина: {settings.get('min_review_length', 100)} симв.")
    print(f"📏 Макс. длина: {settings.get('max_review_length', 400)} симв.")
    print("=" * 50)

    app.run_polling()


if __name__ == "__main__":
    main()