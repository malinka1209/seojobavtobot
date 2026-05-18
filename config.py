# ========== КОНФИГУРАЦИЯ БОТА ==========
# Этот файл можно менять без перезапуска бота

# Цены на подписки (в Telegram Stars)
PRICES = {
    "basic": 25,      # Базовый - 20 отзывов/день
    "pro": 50,        # Про - 100 отзывов/день
    "unlimited": 100, # Безлимит - ∞ отзывов/день
}

# Лимиты подписок (отзывов в день)
LIMITS = {
    "free": 2,
    "basic": 20,
    "pro": 100,
    "unlimited": 999999,
}

# Длительность подписки (в днях)
SUBSCRIPTION_DAYS = 30

# Настройки бота
BOT_SETTINGS = {
    "min_review_length": 100,
    "max_review_length": 400,
    "admin_id": 8017102780,
    "channel_username": "seoavto",
    "support_username": "lynxfix"
}

# Тексты для сообщений
MESSAGES = {
    "welcome": "🤖 <b>SEO Job Bot</b>\n\nПривет, {{name}}!\n{{emoji}} Уровень: {{level}}\n📊 Отзывов: {{reviews}}\n{{plan_emoji}} Подписка: {{plan}}\n📅 Лимит: {{used}}/{{limit}}\n\n📌 Отправь ссылку на инструкцию\n💰 До 300₽ за отзыв!",
    "buy_subscription": "💎 <b>Покупка подписки</b>\n\n📦 Базовый — {{basic}}⭐\n   • 20 отзывов/день\n\n💎 Про — {{pro}}⭐\n   • 100 отзывов/день\n\n👑 Безлимит — {{unlimited}}⭐\n   • Без ограничений\n\n⭐ 1 Star ≈ 2 рубля",
}