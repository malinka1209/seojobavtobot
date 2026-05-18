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

# ========== ПЕРЕМЕННЫЕ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8017102780"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "seoavto")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "lynxfix")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден!")
    exit(1)

# Файлы для хранения данных
USERS_STATS_FILE = "users_stats.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
SETTINGS_FILE = "settings.json"
LEARNING_DATA_FILE = "learning_data.json"
PROMO_FILE = "promocodes.json"
LOG_FOLDER = "bot_logs"

os.makedirs(LOG_FOLDER, exist_ok=True)


# ========== ЗАГРУЗКА ДАННЫХ ==========
def load_json(file, default):
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def save_json(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Загружаем все данные
stats = load_json(USERS_STATS_FILE, {})
subscriptions = load_json(SUBSCRIPTIONS_FILE, {})
settings = load_json(SETTINGS_FILE, {"min_review_length": 100, "max_review_length": 400})
learning_data = load_json(LEARNING_DATA_FILE, {})
promocodes = load_json(PROMO_FILE, {})

# Инициализируем структуру learning_data если её нет
if "stats" not in learning_data:
    learning_data["stats"] = {"total": 0, "good": 0, "bad": 0}
if "good_reviews" not in learning_data:
    learning_data["good_reviews"] = []
if "bad_reviews" not in learning_data:
    learning_data["bad_reviews"] = []
save_json(LEARNING_DATA_FILE, learning_data)

# ========== ТАРИФЫ ПОДПИСОК ==========
SUBSCRIPTION_PLANS = {
    "free": {"name": "Бесплатный", "emoji": "🆓", "daily_limit": 2},
    "basic": {"name": "Базовый", "emoji": "📦", "daily_limit": 20},
    "pro": {"name": "Про", "emoji": "💎", "daily_limit": 100},
    "unlimited": {"name": "Безлимит", "emoji": "👑", "daily_limit": 999999}
}


# ========== СИСТЕМА УРОВНЕЙ ==========
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
    def check_new_achievements(cls, old_count, new_count, user_stats):
        new_achievements = []
        for ach_id, ach_data in cls.ACHIEVEMENTS.items():
            if ach_id not in user_stats.get("achievements", []):
                if new_count >= ach_data["reviews_needed"]:
                    new_achievements.append(ach_data)
        return new_achievements


# ========== САМООБУЧАЮЩИЙСЯ ГЕНЕРАТОР ==========
class SelfLearningGenerator:
    def __init__(self):
        self.min_length = settings.get("min_review_length", 100)
        self.max_length = settings.get("max_review_length", 400)
        self.good_reviews = learning_data.get("good_reviews", [])

    def _ensure_length(self, review):
        while len(review) < self.min_length:
            review += " Очень доволен результатом."
        if len(review) > self.max_length:
            review = review[:self.max_length - 3] + "..."
        return review

    def generate(self, company, instruction_text):
        text_lower = instruction_text.lower()

        # 30% берем из удачных отзывов
        if random.random() < 0.3 and self.good_reviews:
            review = random.choice(self.good_reviews)
            review = review.replace("{company}", company)
        elif 'химчистк' in text_lower:
            review = f"Обратился в {company} за химчисткой. Пятна вывели полностью, вещи как новые. Обязательно обращусь еще!"
        elif 'авто' in text_lower or 'сервис' in text_lower:
            review = f"Обратился в {company} на обслуживание. Мастер всё объяснил, сделали качественно. Буду обращаться еще!"
        elif 'мебель' in text_lower or 'кухн' in text_lower:
            review = f"Заказывал мебель в {company}. Всё сделали качественно и в срок. Результатом очень доволен!"
        else:
            review = f"Обратился в {company} за услугой. Всё сделали качественно. Рекомендую!"

        return self._ensure_length(review), len(review)


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

    address = "не указан"
    addr_match = re.search(r'(?:адрес[:\s]*)([^.\n]+)', full_text, re.IGNORECASE)
    if addr_match:
        address = addr_match.group(1).strip()[:100]

    return {'company': company, 'address': address, 'instruction_text': full_text[:5000]}


def make_maps_url(address):
    if not address or address == "не указан":
        return "https://yandex.ru/maps/"
    return f"https://yandex.ru/maps/?text={urllib.parse.quote(address)}"


# ========== ПРОВЕРКА ЛИМИТОВ ПОДПИСКИ ==========
def check_user_limit(user_id):
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    sub = subscriptions.get(user_id_str, {})
    plan = sub.get("plan", "free")
    expires = sub.get("expires")

    if expires and datetime.now() > datetime.fromisoformat(expires):
        plan = "free"
        subscriptions[user_id_str] = {"plan": "free", "expires": None}
        save_json(SUBSCRIPTIONS_FILE, subscriptions)

    daily_limit = SUBSCRIPTION_PLANS[plan]["daily_limit"]
    user_stats = stats.get(user_id_str, {})
    used_today = user_stats.get("daily_usage", {}).get(today, 0)

    return used_today < daily_limit, daily_limit, used_today, plan


def increment_user_usage(user_id):
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if user_id_str not in stats:
        stats[user_id_str] = {"daily_usage": {}}
    if "daily_usage" not in stats[user_id_str]:
        stats[user_id_str]["daily_usage"] = {}
    stats[user_id_str]["daily_usage"][today] = stats[user_id_str]["daily_usage"].get(today, 0) + 1
    save_json(USERS_STATS_FILE, stats)


# ========== АДМИН-КОМАНДЫ (ПОДПИСКИ) ==========
async def admin_give_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ /give_sub <user_id> <plan> <days>\nПланы: basic, pro, unlimited")
        return
    try:
        user_id = int(args[0])
        plan = args[1].lower()
        days = int(args[2])
        if plan not in SUBSCRIPTION_PLANS or plan == "free":
            await update.message.reply_text(f"❌ План {plan} не существует")
            return
        expires = (datetime.now() + timedelta(days=days)).isoformat()
        subscriptions[str(user_id)] = {"plan": plan, "expires": expires}
        save_json(SUBSCRIPTIONS_FILE, subscriptions)
        await update.message.reply_text(f"✅ Подписка {SUBSCRIPTION_PLANS[plan]['name']} выдана на {days} дней")
    except:
        await update.message.reply_text("❌ Ошибка")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    good = len(learning_data.get("good_reviews", []))
    bad = len(learning_data.get("bad_reviews", []))
    total = learning_data["stats"]["total"]
    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {len(stats)}\n"
        f"📝 Подписок: {len(subscriptions)}\n"
        f"🧠 Оценок ИИ: {good}👍 / {bad}👎\n"
        f"📊 Всего оценок: {total}",
        parse_mode='HTML'
    )


# ========== АДМИН-КОМАНДЫ (ДЛИНА) ==========
async def admin_set_min_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            f"❌ /set_min_length <символы>\nТекущая: {settings.get('min_review_length', 100)}")
        return
    try:
        new_length = int(args[0])
        if new_length < 50:
            await update.message.reply_text("❌ Не меньше 50")
            return
        settings["min_review_length"] = new_length
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text(f"✅ Мин. длина: {new_length} симв.")
    except:
        await update.message.reply_text("❌ Ошибка")


async def admin_set_max_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            f"❌ /set_max_length <символы>\nТекущая: {settings.get('max_review_length', 400)}")
        return
    try:
        new_length = int(args[0])
        if new_length < 100:
            await update.message.reply_text("❌ Не меньше 100")
            return
        settings["max_review_length"] = new_length
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text(f"✅ Макс. длина: {new_length} симв.")
    except:
        await update.message.reply_text("❌ Ошибка")


# ========== АДМИН-КОМАНДЫ (ПРОМОКОДЫ) ==========
async def admin_create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создать промокод: /create_promo basic 30 5"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Использование: /create_promo <plan> <days> <count>\n\n"
            "Планы: basic, pro, unlimited\n"
            "Пример: /create_promo basic 30 5\n"
            "Создаст 5 промокодов на подписку Basic на 30 дней"
        )
        return

    try:
        plan = args[0].lower()
        days = int(args[1])
        count = int(args[2])

        if plan not in ["basic", "pro", "unlimited"]:
            await update.message.reply_text("❌ План должен быть: basic, pro, unlimited")
            return

        created = []
        for i in range(count):
            code = f"{plan.upper()}_{random.randint(1000, 9999)}_{datetime.now().strftime('%d%m%y')}"
            promocodes[code] = {
                "plan": plan,
                "days": days,
                "used": False,
                "created_by": ADMIN_ID,
                "created_at": datetime.now().isoformat()
            }
            created.append(code)

        save_json(PROMO_FILE, promocodes)

        codes_text = "\n".join(created)
        await update.message.reply_text(
            f"✅ Создано {count} промокодов на подписку {plan} ({days} дней):\n\n"
            f"<code>{codes_text}</code>\n\n"
            f"Покупатель должен ввести: /promo КОД",
            parse_mode='HTML'
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def admin_list_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всех промокодов"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    if not promocodes:
        await update.message.reply_text("📭 Нет созданных промокодов")
        return

    active = []
    used = []

    for code, data in promocodes.items():
        if data.get("used", False):
            used.append(code)
        else:
            active.append(code)

    text = f"🎫 <b>Промокоды</b>\n\n"
    text += f"🟢 <b>Активные ({len(active)}):</b>\n"
    for code in active[:10]:
        data = promocodes[code]
        text += f"  • {code} — {data['plan']} ({data['days']} дн.)\n"
    if len(active) > 10:
        text += f"  ... и еще {len(active) - 10}\n"

    text += f"\n🔴 <b>Использованные ({len(used)}):</b>\n"
    for code in used[:5]:
        text += f"  • {code}\n"

    await update.message.reply_text(text, parse_mode='HTML')


async def admin_delete_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить промокод: /delete_promo КОД"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Использование: /delete_promo <код>")
        return

    code = args[0].upper()

    if code not in promocodes:
        await update.message.reply_text("❌ Промокод не найден")
        return

    del promocodes[code]
    save_json(PROMO_FILE, promocodes)
    await update.message.reply_text(f"✅ Промокод {code} удален")


# ========== АКТИВАЦИЯ ПРОМОКОДА ПОЛЬЗОВАТЕЛЕМ ==========
async def use_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активировать промокод: /promo КОД"""
    user = update.effective_user
    user_id = str(user.id)

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "❌ Использование: /promo <код>\n\n"
            "Пример: /promo BASIC_4717_180525\n\n"
            "Код можно получить при покупке подписки"
        )
        return

    code = args[0].upper()

    if code not in promocodes:
        await update.message.reply_text("❌ Неверный промокод!")
        return

    promo = promocodes[code]
    if promo.get("used", False):
        await update.message.reply_text("❌ Этот промокод уже использован!")
        return

    # Активируем подписку
    plan = promo["plan"]
    days = promo["days"]
    expires = (datetime.now() + timedelta(days=days)).isoformat()

    subscriptions[str(user_id)] = {
        "plan": plan,
        "expires": expires,
        "activated_by_promo": code,
        "activated_at": datetime.now().isoformat()
    }
    save_json(SUBSCRIPTIONS_FILE, subscriptions)

    # Отмечаем промокод как использованный
    promocodes[code]["used"] = True
    promocodes[code]["used_by"] = user_id
    promocodes[code]["used_at"] = datetime.now().isoformat()
    save_json(PROMO_FILE, promocodes)

    plan_names = {"basic": "Базовый", "pro": "Про", "unlimited": "Безлимит"}
    await update.message.reply_text(
        f"✅ <b>Подписка активирована!</b>\n\n"
        f"📦 План: {plan_names[plan]}\n"
        f"📅 Дней: {days}\n"
        f"⏰ Действует до: {expires[:10]}\n\n"
        f"Теперь отправляй ссылки на инструкции — бот будет работать без лимитов!",
        parse_mode='HTML'
    )

    # Уведомляем админа
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🎉 Активирован промокод!\n"
             f"👤 Пользователь: {user.first_name} (@{user.username})\n"
             f"🆔 ID: {user_id}\n"
             f"📦 План: {plan_names[plan]} ({days} дней)\n"
             f"🔑 Код: {code}"
    )


# ========== КОМАНДЫ ОБРАТНОЙ СВЯЗИ ==========
async def feedback_good(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("👍 Спасибо! Отзыв сохранен.")
    review = context.user_data.get('last_review', '')
    if review:
        learning_data["good_reviews"].append(review)
        learning_data["stats"]["good"] += 1
        learning_data["stats"]["total"] += 1
        if len(learning_data["good_reviews"]) > 100:
            learning_data["good_reviews"] = learning_data["good_reviews"][-100:]
        save_json(LEARNING_DATA_FILE, learning_data)
    await query.edit_message_reply_markup(reply_markup=None)


async def feedback_bad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("👎 Понял, больше не буду так писать.")
    review = context.user_data.get('last_review', '')
    if review:
        learning_data["bad_reviews"].append(review)
        learning_data["stats"]["bad"] += 1
        learning_data["stats"]["total"] += 1
        if len(learning_data["bad_reviews"]) > 100:
            learning_data["bad_reviews"] = learning_data["bad_reviews"][-100:]
        save_json(LEARNING_DATA_FILE, learning_data)
    await query.edit_message_reply_markup(reply_markup=None)


async def generate_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Генерирую новый вариант...")
    instruction = context.user_data.get('last_instruction')
    company = context.user_data.get('last_company')
    address = context.user_data.get('last_address', '')
    if not instruction:
        await query.edit_message_text("❌ Ошибка")
        return
    generator = SelfLearningGenerator()
    new_review, new_length = generator.generate(company, instruction)
    context.user_data['last_review'] = new_review
    maps_url = make_maps_url(address)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺 Карты", url=maps_url)],
        [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
        [InlineKeyboardButton("🔄 Другой", callback_data="new_review")],
        [InlineKeyboardButton("👍 Хорошо", callback_data="feedback_good")],
        [InlineKeyboardButton("👎 Плохо", callback_data="feedback_bad")]
    ])
    await query.edit_message_text(
        f"🆕 <b>Новый отзыв ({new_length} симв.):</b>\n\n<code>{new_review}</code>",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def copy_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review = context.user_data.get('last_review', '')
    if review:
        await query.message.reply_text(f"📋 <b>Копируй:</b>\n\n<code>{review}</code>", parse_mode='HTML')
    else:
        await query.answer("❌ Ошибка", show_alert=True)


# ========== ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    if user_id not in stats:
        stats[user_id] = {"name": user.first_name, "reviews_count": 0, "achievements": [],
                          "first_seen": datetime.now().isoformat(), "daily_usage": {}}
        save_json(USERS_STATS_FILE, stats)

    review_count = stats[user_id]["reviews_count"]
    level_id, level_data = RankingSystem.get_level(review_count)
    can_use, limit, used, plan = check_user_limit(user.id)
    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Профиль", callback_data="my_profile")],
        [InlineKeyboardButton("📊 Подписка", callback_data="my_subscription")],
        [InlineKeyboardButton("📢 Наш канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👤 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])

    await update.message.reply_text(
        f"🤖 <b>SEO Job Bot</b>\n\n"
        f"👋 Привет, {user.first_name}!\n"
        f"{level_data['emoji']} <b>Уровень:</b> {level_data['name']}\n"
        f"📊 <b>Отзывов:</b> {review_count}\n"
        f"{plan_emoji} <b>Подписка:</b> {plan_name}\n"
        f"📅 <b>Лимит сегодня:</b> {used}/{limit}\n\n"
        f"💰 Купить подписку: https://funpay.com/users/15197528/\n\n"
        f"📌 Отправь ссылку на инструкцию — бот напишет отзыв!\n"
        f"🎁 Бесплатно 2 отзыва в день — тестируй!\n\n"
        f"📢 @{CHANNEL_USERNAME}",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = str(user.id)

    if user_id not in stats:
        stats[user_id] = {"reviews_count": 0, "achievements": []}
        save_json(USERS_STATS_FILE, stats)

    review_count = stats[user_id]["reviews_count"]
    level_id, level_data = RankingSystem.get_level(review_count)
    achievements = stats[user_id].get("achievements", [])

    ach_text = ""
    for ach_id, ach_data in RankingSystem.ACHIEVEMENTS.items():
        if ach_id in achievements:
            ach_text += f"✅ {ach_data['name']}\n"
        else:
            ach_text += f"⬜ {ach_data['name']}\n"

    text = f"🏆 <b>ПРОФИЛЬ</b>\n\n"
    text += f"👤 {user.first_name}\n"
    text += f"{level_data['emoji']} {level_data['name']}\n"
    text += f"📊 Отзывов: {review_count}\n\n"
    text += f"<b>🏅 ДОСТИЖЕНИЯ:</b>\n{ach_text}"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]])
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)


async def my_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
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
        text += f"💎 Купить подписку: https://funpay.com/users/15197528/"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]])
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = str(user.id)
    review_count = stats.get(user_id, {}).get("reviews_count", 0)
    level_id, level_data = RankingSystem.get_level(review_count)
    can_use, limit, used, plan = check_user_limit(user.id)
    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Профиль", callback_data="my_profile")],
        [InlineKeyboardButton("📊 Подписка", callback_data="my_subscription")],
        [InlineKeyboardButton("📢 Наш канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👤 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])

    await query.edit_message_text(
        f"🤖 <b>SEO Job Bot</b>\n\n"
        f"👋 {user.first_name}\n"
        f"{level_data['emoji']} {level_data['name']}\n"
        f"📊 Отзывов: {review_count}\n"
        f"{plan_emoji} {plan_name} ({used}/{limit})\n\n"
        f"📌 Отправь ссылку на инструкцию",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Твой ID: `{update.effective_user.id}`", parse_mode='Markdown')


# ========== ОСНОВНОЙ ОБРАБОТЧИК ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.effective_user

    if not url.startswith("https://instructions.jobseo.ru/"):
        await update.message.reply_text("❌ Неверная ссылка!\nНужно: https://instructions.jobseo.ru/p/...")
        return

    can_use, limit, used, plan = check_user_limit(user.id)
    if not can_use:
        plan_name = SUBSCRIPTION_PLANS[plan]["name"]
        await update.message.reply_text(
            f"❌ <b>Лимит исчерпан!</b>\n\n"
            f"Тариф: {plan_name}\n"
            f"Лимит: {limit} отзывов/день\n"
            f"Использовано: {used}/{limit}\n\n"
            f"💎 Купи подписку: https://funpay.com/users/15197528/",
            parse_mode='HTML'
        )
        return

    status_msg = await update.message.reply_text("🧠 ИИ анализирует инструкцию...")

    try:
        data = parse_instruction(url)
        generator = SelfLearningGenerator()
        review, review_length = generator.generate(data['company'], data['instruction_text'])
        maps_url = make_maps_url(data['address'])

        # Обновляем статистику пользователя
        user_id = str(user.id)
        if user_id not in stats:
            stats[user_id] = {"name": user.first_name, "reviews_count": 0, "achievements": [],
                              "first_seen": datetime.now().isoformat(), "daily_usage": {}}

        old_count = stats[user_id].get("reviews_count", 0)
        new_count = old_count + 1
        stats[user_id]["reviews_count"] = new_count

        today = datetime.now().strftime("%Y-%m-%d")
        if "daily_usage" not in stats[user_id]:
            stats[user_id]["daily_usage"] = {}
        stats[user_id]["daily_usage"][today] = stats[user_id]["daily_usage"].get(today, 0) + 1

        new_achievements = RankingSystem.check_new_achievements(old_count, new_count, stats[user_id])
        for ach in new_achievements:
            if "achievements" not in stats[user_id]:
                stats[user_id]["achievements"] = []
            stats[user_id]["achievements"].append(ach["name"])

        save_json(USERS_STATS_FILE, stats)
        increment_user_usage(user.id)

        level_id, level_data = RankingSystem.get_level(new_count)

        level_up = ""
        if level_id > 0 and old_count < RankingSystem.LEVELS[level_id]["min_reviews"] <= new_count:
            level_up = f"\n\n🎉 <b>НОВЫЙ УРОВЕНЬ: {level_data['name']}</b>"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗺 Открыть Карты", url=maps_url)],
            [InlineKeyboardButton("📋 Копировать отзыв", callback_data="copy_review")],
            [InlineKeyboardButton("🔄 Другой вариант", callback_data="new_review")],
            [InlineKeyboardButton("👍 Хороший отзыв", callback_data="feedback_good")],
            [InlineKeyboardButton("👎 Плохой отзыв", callback_data="feedback_bad")]
        ])

        context.user_data['last_review'] = review
        context.user_data['last_instruction'] = data['instruction_text']
        context.user_data['last_company'] = data['company']
        context.user_data['last_address'] = data['address']

        await status_msg.edit_text(
            f"✅ <b>{data['company']}</b>\n📍 {data['address']}\n\n"
            f"📝 <b>Отзыв ({review_length} символов):</b>\n"
            f"<code>{review}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{level_data['emoji']} <b>Уровень:</b> {level_data['name']}\n"
            f"📊 <b>Всего отзывов:</b> {new_count}{level_up}\n"
            f"📅 <b>Лимит сегодня:</b> {used + 1}/{limit}\n"
            f"🧠 <b>Оцени отзыв:</b> 👍 или 👎 (ИИ учится!)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📌 ИНСТРУКЦИЯ:</b>\n\n"
            f"1️⃣ Нажми «Открыть Карты»\n"
            f"2️⃣ Найди компанию → «Отзывы»\n"
            f"3️⃣ Поставь 5★ → Вставь отзыв\n"
            f"4️⃣ Отправь → Сделай скриншот\n"
            f"5️⃣ Отправь скриншот в @jobseo_bot\n\n"
            f"💰 <b>После проверки — оплата на счёт!</b>",
            parse_mode='HTML',
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

    except Exception as e:
        await status_msg.edit_text(
            f"❌ Ошибка: {str(e)[:100]}\n\n👨‍💻 @{SUPPORT_USERNAME}",
            parse_mode='HTML'
        )


# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Админ-команды (подписки)
    app.add_handler(CommandHandler("give_sub", admin_give_subscription))

    # Админ-команды (длина)
    app.add_handler(CommandHandler("set_min_length", admin_set_min_length))
    app.add_handler(CommandHandler("set_max_length", admin_set_max_length))

    # Админ-команды (промокоды)
    app.add_handler(CommandHandler("create_promo", admin_create_promo))
    app.add_handler(CommandHandler("list_promos", admin_list_promos))
    app.add_handler(CommandHandler("delete_promo", admin_delete_promo))

    # Админ-команды (статистика)
    app.add_handler(CommandHandler("stats", admin_stats))

    # Пользовательские команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("promo", use_promo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback-обработчики
    app.add_handler(CallbackQueryHandler(my_profile, pattern="^my_profile$"))
    app.add_handler(CallbackQueryHandler(my_subscription, pattern="^my_subscription$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(copy_review_callback, pattern="^copy_review$"))
    app.add_handler(CallbackQueryHandler(generate_new, pattern="^new_review$"))
    app.add_handler(CallbackQueryHandler(feedback_good, pattern="^feedback_good$"))
    app.add_handler(CallbackQueryHandler(feedback_bad, pattern="^feedback_bad$"))

    print("=" * 55)
    print("✅ БОТ ЗАПУЩЕН!")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"📏 Длина отзыва: {settings.get('min_review_length', 100)}-{settings.get('max_review_length', 400)} симв.")
    print(f"🧠 Самообучение: {learning_data['stats']['total']} оценок")
    print(f"🎫 Промокодов: {len(promocodes)}")
    print("=" * 55)
    print("\n📋 АДМИН-КОМАНДЫ:")
    print("  /create_promo basic 30 5  — создать 5 промокодов")
    print("  /list_promos              — список промокодов")
    print("  /delete_promo КОД         — удалить промокод")
    print("  /give_sub ID plan days    — выдать подписку")
    print("  /set_min_length 150       — мин. длина отзыва")
    print("  /set_max_length 500       — макс. длина отзыва")
    print("  /stats                    — статистика бота")
    print("=" * 55)

    app.run_polling()


if __name__ == "__main__":
    main()