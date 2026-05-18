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

# ========== ПЕРЕМЕННЫЕ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = "8607583029:AAGfmzzGlYcE7oEQ_Y2cOeOGIgqnCN_391s"

ADMIN_ID = int(os.getenv("ADMIN_ID", "8017102780"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "seoavto")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "lynxfix")

# ========== НАСТРОЙКИ (можно менять админом) ==========
MIN_REVIEW_LENGTH = 100  # минимальная длина отзыва в символах

# Файлы для хранения данных
USERS_STATS_FILE = "users_stats.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
LEARNING_DATA_FILE = "learning_data.json"
SETTINGS_FILE = "settings.json"


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"min_length": 100}


def save_settings(settings):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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


def load_learning_data():
    if os.path.exists(LEARNING_DATA_FILE):
        with open(LEARNING_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"good_reviews": [], "bad_reviews": [], "user_corrections": []}


def save_learning_data(data):
    with open(LEARNING_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


LOG_FOLDER = "bot_logs"
os.makedirs(LOG_FOLDER, exist_ok=True)

# ========== ТАРИФЫ ПОДПИСОК ==========
SUBSCRIPTION_PLANS = {
    "free": {"name": "Бесплатный", "emoji": "🆓", "daily_limit": 2, "price": 0, "days": 0},
    "basic": {"name": "Базовый", "emoji": "📦", "daily_limit": 20, "price": 199, "days": 30},
    "pro": {"name": "Про", "emoji": "💎", "daily_limit": 100, "price": 499, "days": 30},
    "unlimited": {"name": "Безлимит", "emoji": "👑", "daily_limit": 999999, "price": 999, "days": 30}
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
    def check_new_achievements(cls, user_id, old_count, new_count, stats):
        new_achievements = []
        for ach_id, ach_data in cls.ACHIEVEMENTS.items():
            if ach_id not in stats.get("achievements", []):
                if new_count >= ach_data["reviews_needed"]:
                    new_achievements.append(ach_data)
        return new_achievements


# ========== ОБУЧАЕМЫЙ ГЕНЕРАТОР С ЖЕСТКИМ КОНТРОЛЕМ ДЛИНЫ ==========
class LearningReviewGenerator:

    def __init__(self):
        self.learning_data = load_learning_data()

        # Базовые шаблоны для разных тем
        self.base_templates = {
            "auto": [
                "Поменял {item} в {company}. Цену сказали сразу, без сюрпризов. Всё сделали качественно.",
                "Машина стучала. В {company} нашли и заменили {item}. Всё чётко, доволен результатом.",
                "Заехал на ТО в {company}. Помимо регламента проверили ходовую. Спасибо за работу!",
                "Сделал {item} в {company}. Доволен, буду обращаться ещё. Рекомендую!"
            ],
            "furniture": [
                "Заказал {item} в {company}. Сделали в срок, качество отличное. Мастер приехал вовремя.",
                "Кухню делали в {company}. Замерщик приехал вовремя, установили аккуратно. Всё понравилось.",
                "Мебель из {company} — отличное качество. Всё как договаривались. Спасибо!"
            ],
            "cleaning": [
                "Сдал {item} в {company}. Пятна вывели, вещь как новая. Забрал через 2 дня. Отличный сервис.",
                "Переживал за {item} — думал, испортят. Нет, всё отлично. Вещь свежая и чистая.",
                "Зимние {item} были в ужасном состоянии. В {company} восстановили. Теперь как новые."
            ],
            "general": [
                "Обратился в {company} за {item}. Всё сделали как договаривались. Доволен результатом.",
                "Услугой в {company} доволен. Спасибо за работу. Буду обращаться ещё."
            ]
        }

        # Фразы для удлинения отзыва (без повторов)
        self.expand_phrases = [
            " Цены адекватные.",
            " Персонал вежливый.",
            " Сделали быстро.",
            " Качество на высоте.",
            " Обязательно приду ещё.",
            " Всем советую.",
            " Спасибо за работу!",
            " Доволен результатом.",
            " Всё понравилось."
        ]

    def detect_topic(self, text):
        text_lower = text.lower()

        # АВТО
        auto_keywords = ['авто', 'машина', 'сервис', 'то', 'масло', 'двигатель', 'vag', 'шиномонтаж', 'автосервис',
                         'замена масла', 'ходовая', 'подвеска']
        for kw in auto_keywords:
            if kw in text_lower:
                return "auto"

        # МЕБЕЛЬ
        furniture_keywords = ['мебель', 'шкаф', 'кухня', 'диван', 'кровать', 'стол', 'стул', 'комод', 'матрас']
        for kw in furniture_keywords:
            if kw in text_lower:
                return "furniture"

        # ХИМЧИСТКА
        cleaning_keywords = ['химчистк', 'чистк', 'пятно', 'пуховик', 'шуба', 'куртка', 'обувь', 'ковёр']
        for kw in cleaning_keywords:
            if kw in text_lower:
                return "cleaning"

        return "general"

    def extract_item(self, text, topic):
        text_lower = text.lower()

        if topic == "auto":
            items = ['масло', 'фильтр', 'колодки', 'ходовую', 'двигатель', 'подвеску', 'ремонт']
            for item in items:
                if item in text_lower:
                    return item
            return "машину"

        elif topic == "furniture":
            items = ['шкаф', 'кухню', 'диван', 'кровать', 'стол', 'стул', 'комод']
            for item in items:
                if item in text_lower:
                    return item
            return "мебель"

        elif topic == "cleaning":
            items = ['пуховик', 'куртку', 'шубу', 'кроссовки', 'свитер', 'пальто', 'ковёр']
            for item in items:
                if item in text_lower:
                    return item
            return "вещь"

        else:
            return "услугу"

    def extract_company(self, text):
        words = text.split()
        for i, word in enumerate(words):
            if word.lower() in ['в', 'у', 'компании', 'салоне', 'сервисе'] and i + 1 < len(words):
                return words[i + 1].strip(',.!')
            if word and word[0].isupper() and len(word) > 2:
                if word.lower() not in ['Авто', 'Машина', 'Сервис', 'Мебель']:
                    return word.strip(',.!')
        return "компанию"

    def enforce_length(self, review, min_length):
        """Жестко добивает отзыв до нужной длины"""
        used_phrases = set()

        while len(review) < min_length:
            # Выбираем фразу, которой еще не было
            available = [p for p in self.expand_phrases if p not in used_phrases]
            if not available:
                available = self.expand_phrases

            new_phrase = random.choice(available)
            used_phrases.add(new_phrase)
            review += new_phrase

            # Защита от бесконечного цикла
            if len(used_phrases) > len(self.expand_phrases) * 2:
                review += " Очень доволен."
                break

        return review

    def generate(self, user_input, instruction_text=None, min_length=100):
        """Генерирует отзыв с железным соблюдением длины"""

        # Определяем тему
        topic = self.detect_topic(user_input)
        item = self.extract_item(user_input, topic)
        company = self.extract_company(user_input)

        # Если есть инструкция — пробуем уточнить тему
        if instruction_text:
            inst_lower = instruction_text.lower()
            if 'авто' in inst_lower or 'масло' in inst_lower:
                topic = "auto"
            elif 'мебель' in inst_lower or 'шкаф' in inst_lower:
                topic = "furniture"
            elif 'химчистка' in inst_lower or 'чистка' in inst_lower:
                topic = "cleaning"

        # Берем шаблон
        templates = self.base_templates.get(topic, self.base_templates["general"])
        template = random.choice(templates)
        review = template.format(company=company, item=item)

        # Жестко добиваем до нужной длины
        review = self.enforce_length(review, min_length)

        return review, topic, item, company

    def learn_from_feedback(self, user_input, generated_review, feedback, corrected_review=None):
        if feedback == "good":
            if generated_review not in self.learning_data.get("good_reviews", []):
                if "good_reviews" not in self.learning_data:
                    self.learning_data["good_reviews"] = []
                self.learning_data["good_reviews"].append(generated_review)
                if len(self.learning_data["good_reviews"]) > 50:
                    self.learning_data["good_reviews"] = self.learning_data["good_reviews"][-50:]

        elif feedback == "bad":
            if generated_review not in self.learning_data.get("bad_reviews", []):
                if "bad_reviews" not in self.learning_data:
                    self.learning_data["bad_reviews"] = []
                self.learning_data["bad_reviews"].append(generated_review)
                if len(self.learning_data["bad_reviews"]) > 50:
                    self.learning_data["bad_reviews"] = self.learning_data["bad_reviews"][-50:]

        elif feedback == "corrected" and corrected_review:
            self.learning_data["user_corrections"].append({
                "original": generated_review,
                "corrected": corrected_review,
                "user_input": user_input,
                "timestamp": datetime.now().isoformat()
            })
            if len(self.learning_data["user_corrections"]) > 100:
                self.learning_data["user_corrections"] = self.learning_data["user_corrections"][-100:]

        save_learning_data(self.learning_data)
        return True


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

    if today not in stats[user_id_str].get("daily_usage", {}):
        stats[user_id_str]["daily_usage"] = {today: 0}

    used_today = stats[user_id_str]["daily_usage"][today]

    if used_today >= daily_limit:
        return False, daily_limit, used_today, plan

    return True, daily_limit, used_today, plan


def increment_user_usage(user_id):
    stats = load_stats()
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    if user_id_str not in stats:
        stats[user_id_str] = {"daily_usage": {}}

    if today not in stats[user_id_str].get("daily_usage", {}):
        stats[user_id_str]["daily_usage"] = {today: 0}

    stats[user_id_str]["daily_usage"][today] += 1
    save_stats(stats)


# ========== ПАРСИНГ ИНСТРУКЦИИ ==========
def parse_instruction(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    full_text = soup.get_text()

    if 'яндекс.карт' not in full_text.lower():
        raise Exception("❌ Это задание НЕ с Яндекс.Картами!")

    lines = full_text.split('\n')
    company = "Компания"
    for line in lines[:5]:
        line = line.strip()
        if line and len(line) < 50 and not any(x in line.lower() for x in ['шаг', 'стоимость', 'важно']):
            company = line.split(',')[0].strip()
            break

    address = "не указан"
    addr_patterns = [r'(площадь[^,\n]+)', r'(ул\.[^,\n]+)', r'(наб\.[^,\n]+)']
    for pattern in addr_patterns:
        match = re.search(pattern, full_text)
        if match:
            address = match.group(0).strip()
            break

    return {'company': company, 'address': address, 'instruction_text': full_text[:8000]}


def make_maps_url(address):
    if not address or address == "не указан":
        return "https://yandex.ru/maps/"
    encoded = urllib.parse.quote(address)
    return f"https://yandex.ru/maps/?text={encoded}"


# ========== АДМИН-КОМАНДЫ ==========
async def admin_set_min_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить минимальную длину отзыва"""
    global MIN_REVIEW_LENGTH

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(f"❌ /set_min_length <число>\nТекущая: {MIN_REVIEW_LENGTH} символов")
        return

    try:
        new_length = int(args[0])
        if new_length < 50:
            await update.message.reply_text("❌ Минимум 50 символов")
            return
        if new_length > 500:
            await update.message.reply_text("❌ Максимум 500 символов")
            return

        MIN_REVIEW_LENGTH = new_length
        settings = load_settings()
        settings["min_length"] = new_length
        save_settings(settings)

        await update.message.reply_text(f"✅ Минимальная длина отзыва установлена: {new_length} символов")
    except:
        await update.message.reply_text("❌ Неверный формат")


async def admin_show_min_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущую минимальную длину"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    await update.message.reply_text(f"📏 Текущая минимальная длина отзыва: {MIN_REVIEW_LENGTH} символов")


async def admin_give_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ /give_sub <user_id> <plan> <days>\nПланы: free, basic, pro, unlimited")
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
        subscriptions[str(user_id)] = {"plan": plan, "expires": expires, "given_by": ADMIN_ID}
        save_subscriptions(subscriptions)

        plan_name = SUBSCRIPTION_PLANS[plan]["name"]
        await update.message.reply_text(f"✅ Подписка {plan_name} выдана {user_id} до {expires[:10]}")
    except:
        await update.message.reply_text("❌ Неверный формат")


async def admin_remove_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ /remove_sub <user_id>")
        return

    try:
        user_id = int(args[0])
        subscriptions = load_subscriptions()
        if str(user_id) in subscriptions:
            del subscriptions[str(user_id)]
            save_subscriptions(subscriptions)
            await update.message.reply_text(f"✅ Подписка {user_id} удалена")
    except:
        await update.message.reply_text("❌ Неверный формат")


async def admin_check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ /check_sub <user_id>")
        return

    try:
        user_id = int(args[0])
        subscriptions = load_subscriptions()
        sub = subscriptions.get(str(user_id), {"plan": "free"})
        plan = sub.get("plan", "free")
        expires = sub.get("expires")
        plan_name = SUBSCRIPTION_PLANS[plan]["name"]

        text = f"📊 {user_id}: {plan_name}"
        if expires:
            text += f" до {expires[:10]}"
        await update.message.reply_text(text)
    except:
        await update.message.reply_text("❌ Неверный формат")


async def admin_list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    subscriptions = load_subscriptions()
    active = []
    for user_id, sub in subscriptions.items():
        if sub.get("expires") and datetime.now() < datetime.fromisoformat(sub["expires"]):
            active.append((user_id, sub))

    if not active:
        await update.message.reply_text("📭 Нет активных подписок")
        return

    text = "📊 Активные подписки:\n"
    for user_id, sub in active:
        plan = SUBSCRIPTION_PLANS[sub["plan"]]["name"]
        expires = sub["expires"][:10]
        text += f"👤 {user_id} — {plan} (до {expires})\n"
    await update.message.reply_text(text)


async def admin_learn_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    learning_data = load_learning_data()
    good = len(learning_data.get("good_reviews", []))
    bad = len(learning_data.get("bad_reviews", []))
    corrections = len(learning_data.get("user_corrections", []))

    await update.message.reply_text(
        f"📊 <b>Обучение бота</b>\n\n"
        f"✅ Хороших отзывов: {good}\n"
        f"❌ Плохих отзывов: {bad}\n"
        f"✏️ Исправлений: {corrections}\n\n"
        f"📏 Мин. длина: {MIN_REVIEW_LENGTH} симв.",
        parse_mode='HTML'
    )


async def admin_clear_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    save_learning_data({"good_reviews": [], "bad_reviews": [], "user_corrections": []})
    await update.message.reply_text("🗑 Обучение сброшено!")


async def admin_test_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен")
        return

    user_input = " ".join(context.args) if context.args else "химчистка пуховика Leda"

    generator = LearningReviewGenerator()
    review, topic, item, company = generator.generate(user_input, min_length=MIN_REVIEW_LENGTH)

    context.user_data['last_admin_test'] = {
        'user_input': user_input,
        'review': review,
        'topic': topic,
        'item': item,
        'company': company
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👍 Хорошо", callback_data="admin_good"),
         InlineKeyboardButton("👎 Плохо", callback_data="admin_bad")],
        [InlineKeyboardButton("✏️ Исправить", callback_data="admin_correct")]
    ])

    await update.message.reply_text(
        f"🧪 <b>Тестовый отзыв</b>\n\n"
        f"Запрос: {user_input}\n"
        f"Тема: {topic}\n"
        f"Длина: {len(review)} симв. (мин. {MIN_REVIEW_LENGTH})\n\n"
        f"📝 {review}\n\n"
        f"Оцени:", parse_mode='HTML', reply_markup=keyboard
    )


# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    stats = load_stats()
    if user_id not in stats:
        stats[user_id] = {"name": user.first_name, "reviews_count": 0, "achievements": [],
                          "first_seen": datetime.now().isoformat(), "daily_usage": {}}
        save_stats(stats)

    review_count = stats[user_id]["reviews_count"]
    level_id, level_data = RankingSystem.get_level(review_count)
    can_use, limit, used, plan = check_user_limit(user.id)
    plan_name = SUBSCRIPTION_PLANS[plan]["name"]
    plan_emoji = SUBSCRIPTION_PLANS[plan]["emoji"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Мой профиль", callback_data="my_profile")],
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
        f"📅 <b>Лимит сегодня:</b> {used}/{limit}\n"
        f"📏 <b>Мин. длина отзыва:</b> {MIN_REVIEW_LENGTH} симв.\n\n"
        f"📌 Отправь ссылку на инструкцию — получу готовый отзыв\n"
        f"💰 До 300₽ за отзыв!\n\n"
        f"📢 @{CHANNEL_USERNAME}",
        parse_mode='HTML', reply_markup=keyboard
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

    next_level_id = level_id + 1
    if next_level_id in RankingSystem.LEVELS:
        next_level = RankingSystem.LEVELS[next_level_id]
        reviews_needed = next_level["min_reviews"] - review_count
        progress_text = f"📈 До {next_level['name']}: {reviews_needed} отзывов"
    else:
        progress_text = "🏆 Максимальный уровень!"

    achievements_text = ""
    for ach_id, ach_data in RankingSystem.ACHIEVEMENTS.items():
        if ach_id in achievements:
            achievements_text += f"✅ {ach_data['name']}\n"
        else:
            achievements_text += f"⬜ {ach_data['name']}\n"

    text = f"🏆 <b>ПРОФИЛЬ</b>\n\n👤 {user.first_name}\n{level_data['emoji']} {level_data['name']}\n📊 {review_count} отзывов\n{progress_text}\n\n<b>🏅 ДОСТИЖЕНИЯ:</b>\n{achievements_text}\n📅 с {stats[user_id]['first_seen'][:10]}"

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

    text = f"{plan_emoji} <b>Подписка</b>\n\n📦 {plan_name}\n📅 Лимит: {daily_limit} отзывов/день\n📊 Сегодня: {used}/{daily_limit}"
    if expires:
        text += f"\n⏰ до {expires[:10]}"

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
        f"🤖 <b>SEO Job Bot</b>\n\n👋 {user.first_name}\n{level_data['emoji']} {level_data['name']}\n📊 {review_count} отзывов\n{plan_emoji} {plan_name}\n📅 {used}/{limit}\n\n📌 Отправь ссылку на инструкцию",
        parse_mode='HTML', reply_markup=keyboard
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.effective_user
    user_id = str(user.id)

    if not url.startswith("https://instructions.jobseo.ru/"):
        await update.message.reply_text("❌ Неверная ссылка!\nНужно: https://instructions.jobseo.ru/p/...")
        return

    can_use, limit, used, plan = check_user_limit(user.id)
    if not can_use:
        await update.message.reply_text(f"❌ Лимит исчерпан!\n📊 {used}/{limit}\n👤 @{SUPPORT_USERNAME}")
        return

    status_msg = await update.message.reply_text("🔍 Анализирую инструкцию...")

    try:
        data = parse_instruction(url)

        learned_gen = LearningReviewGenerator()
        review, topic, item, company = learned_gen.generate(
            data['company'],
            data['instruction_text'],
            min_length=MIN_REVIEW_LENGTH
        )

        maps_url = make_maps_url(data['address'])

        # Обновляем статистику
        stats = load_stats()
        if user_id not in stats:
            stats[user_id] = {"name": user.first_name, "reviews_count": 0, "achievements": [],
                              "first_seen": datetime.now().isoformat(), "daily_usage": {}}

        old_count = stats[user_id]["reviews_count"]
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

        level_id, level_data = RankingSystem.get_level(new_count)

        level_up_text = ""
        if level_id > 0 and old_count < RankingSystem.LEVELS[level_id]["min_reviews"] <= new_count:
            level_up_text = f"\n\n🎉 <b>НОВЫЙ УРОВЕНЬ: {level_data['name']} {level_data['emoji']}</b>"

        achievements_text = ""
        if new_achievements:
            achievements_text = "\n\n🏅 <b>ДОСТИЖЕНИЯ:</b>\n" + "\n".join(
                [f"✅ {ach['name']}" for ach in new_achievements])

        increment_user_usage(user.id)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗺 Карты", url=maps_url)],
            [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
            [InlineKeyboardButton("🔄 Другой", callback_data="new_review")],
            [InlineKeyboardButton("🏆 Профиль", callback_data="my_profile")],
            [InlineKeyboardButton("📊 Подписка", callback_data="my_subscription")]
        ])

        context.user_data['last_review'] = review
        context.user_data['last_instruction'] = data['instruction_text']
        context.user_data['last_company'] = data['company']
        context.user_data['last_address'] = data['address']

        await status_msg.edit_text(
            f"✅ <b>{data['company']}</b>\n📍 {data['address']}\n\n"
            f"📝 <b>Отзыв ({len(review)} симв., мин. {MIN_REVIEW_LENGTH}):</b>\n"
            f"<code>{review}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{level_data['emoji']} <b>Уровень:</b> {level_data['name']}\n"
            f"📊 <b>Отзывов:</b> {new_count}\n"
            f"📅 <b>Лимит:</b> {used + 1}/{limit}{level_up_text}{achievements_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📌 ИНСТРУКЦИЯ:</b>\n"
            f"1️⃣ Карты → 2️⃣ Отзывы → 3️⃣ 5★\n"
            f"4️⃣ Вставить отзыв → 5️⃣ Отправить\n"
            f"6️⃣ Скриншот → 7️⃣ @jobseo_bot\n\n"
            f"💰 <b>Оплата после проверки!</b>",
            parse_mode='HTML', reply_markup=keyboard, disable_web_page_preview=True
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}\n👨‍💻 @{SUPPORT_USERNAME}", parse_mode='HTML')


async def copy_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review = context.user_data.get('last_review', '')
    if review:
        await query.message.reply_text(f"📋 <b>Скопируй:</b>\n\n<code>{review}</code>", parse_mode='HTML')
    else:
        await query.answer("❌ Ошибка", show_alert=True)


async def new_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Генерирую...")

    instruction = context.user_data.get('last_instruction')
    company = context.user_data.get('last_company')
    address = context.user_data.get('last_address', '')

    if not instruction:
        await query.edit_message_text("❌ Ошибка, отправь ссылку заново")
        return

    learned_gen = LearningReviewGenerator()
    new_review, topic, item, company = learned_gen.generate(company, instruction, min_length=MIN_REVIEW_LENGTH)
    context.user_data['last_review'] = new_review

    maps_url = make_maps_url(address)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Копировать", callback_data="copy_review")],
        [InlineKeyboardButton("🔄 Еще", callback_data="new_review")],
        [InlineKeyboardButton("🗺 Карты", url=maps_url)]
    ])

    await query.edit_message_text(
        f"🆕 <b>Новый отзыв</b> ({len(new_review)} симв.):\n\n<code>{new_review}</code>",
        parse_mode='HTML', reply_markup=keyboard
    )


# ========== АДМИН-ОБУЧЕНИЕ (callbacks) ==========
async def admin_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Доступ запрещен")
        return

    feedback = query.data.split('_')[1]
    last_data = context.user_data.get('last_admin_test', {})

    generator = LearningReviewGenerator()

    if feedback == "good":
        generator.learn_from_feedback(last_data.get('user_input', ''), last_data.get('review', ''), "good")
        await query.edit_message_text("✅ Хороший отзыв сохранён!", parse_mode='HTML')
    elif feedback == "bad":
        generator.learn_from_feedback(last_data.get('user_input', ''), last_data.get('review', ''), "bad")
        await query.edit_message_text("⚠️ Плохой отзыв отмечен. Больше не буду так писать.", parse_mode='HTML')
    elif feedback == "correct":
        await query.edit_message_text("✏️ Напиши свой вариант отзыва в ответ на это сообщение:")
        context.user_data['waiting_for_admin_correction'] = True
        context.user_data['admin_correct_data'] = last_data


async def handle_admin_correction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_admin_correction'):
        return
    if update.effective_user.id != ADMIN_ID:
        return

    corrected = update.message.text.strip()
    last_data = context.user_data.get('admin_correct_data', {})

    generator = LearningReviewGenerator()
    generator.learn_from_feedback(
        last_data.get('user_input', ''),
        last_data.get('review', ''),
        "corrected",
        corrected
    )

    context.user_data['waiting_for_admin_correction'] = False
    context.user_data['admin_correct_data'] = None

    await update.message.reply_text(f"✅ Запомнил твой вариант:\n\n{corrected}")


# ========== ЗАПУСК ==========
def main():
    global MIN_REVIEW_LENGTH
    settings = load_settings()
    MIN_REVIEW_LENGTH = settings.get("min_length", 100)

    app = Application.builder().token(BOT_TOKEN).build()

    # Админ-команды (настройки длины)
    app.add_handler(CommandHandler("set_min_length", admin_set_min_length))
    app.add_handler(CommandHandler("show_min_length", admin_show_min_length))

    # Админ-команды (подписки)
    app.add_handler(CommandHandler("give_sub", admin_give_subscription))
    app.add_handler(CommandHandler("remove_sub", admin_remove_subscription))
    app.add_handler(CommandHandler("check_sub", admin_check_subscription))
    app.add_handler(CommandHandler("list_subs", admin_list_subscriptions))

    # Админ-команды (обучение)
    app.add_handler(CommandHandler("learn_stats", admin_learn_stats))
    app.add_handler(CommandHandler("clear_learning", admin_clear_learning))
    app.add_handler(CommandHandler("test_review", admin_test_review))

    # Пользовательские
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback-обработчики
    app.add_handler(CallbackQueryHandler(my_profile, pattern="^my_profile$"))
    app.add_handler(CallbackQueryHandler(my_subscription, pattern="^my_subscription$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(copy_review_callback, pattern="^copy_review$"))
    app.add_handler(CallbackQueryHandler(new_review_callback, pattern="^new_review$"))

    # Админ-обучение callbacks
    app.add_handler(CallbackQueryHandler(admin_feedback_callback, pattern="^admin_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_correction))

    print("=" * 60)
    print("✅ БОТ ЗАПУЩЕН (ПОЛНАЯ ВЕРСИЯ)")
    print("")
    print(f"📏 МИНИМАЛЬНАЯ ДЛИНА ОТЗЫВА: {MIN_REVIEW_LENGTH} символов")
    print("   /set_min_length <число> — изменить")
    print("")
    print("👑 АДМИН-КОМАНДЫ ПОДПИСОК:")
    print("   /give_sub <user_id> <plan> <days>")
    print("   /remove_sub <user_id>")
    print("   /check_sub <user_id>")
    print("   /list_subs")
    print("")
    print("🧠 АДМИН-КОМАНДЫ ОБУЧЕНИЯ:")
    print("   /learn_stats — статистика")
    print("   /clear_learning — сброс")
    print("   /test_review [тема] — тест")
    print("=" * 60)

    app.run_polling()


if __name__ == "__main__":
    main()