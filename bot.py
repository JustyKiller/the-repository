import telebot
from telebot import types
import time
import os
import logging
import sys
from threading import Thread

# ===== НАСТРОЙКА ЛОГИРОВАНИЯ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ===== НАСТРОЙКИ (через переменные окружения) =====
# ВАЖНО: Токен и ID теперь берутся из переменных окружения!
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))
CHANNEL_ID = os.environ.get('CHANNEL_ID')

# Проверяем, что все переменные установлены
if not TOKEN:
    logger.error("BOT_TOKEN не установлен! Добавь его в переменные окружения.")
    sys.exit(1)
if not ADMIN_ID:
    logger.error("ADMIN_ID не установлен! Добавь его в переменные окружения.")
    sys.exit(1)
if not CHANNEL_ID:
    logger.error("CHANNEL_ID не установлен! Добавь его в переменные окружения.")
    sys.exit(1)

logger.info(f"Бот запускается для админа {ADMIN_ID} и канала {CHANNEL_ID}")

# Инициализация бота
bot = telebot.TeleBot(TOKEN)

# Списки для управления состоянием
waiting_for_message = set()
admin_messages = {}  # message_id -> (user_id, original_message)

# ===== КУЛДАУН (5 минут) =====
COOLDOWN_SECONDS = 5 * 60  
user_cooldowns = {}  # user_id -> last_send_time

def format_time(seconds):
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

def check_cooldown(user_id):
    now = time.time()
    last_time = user_cooldowns.get(user_id, 0)
    passed = now - last_time

    if passed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - passed)
        return False, format_time(remaining)

    return True, None

# ===== КОМАНДА /START =====
@bot.message_handler(commands=['start'])
def start(message):
    logger.info(f"Пользователь {message.from_user.id} запустил /start")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✍️ Написать сообщение", callback_data="write_msg"))

    welcome_text = (
        f"Привет! 👋\n\n"
        f"Чтобы написать сообщение (предложку) в канал, "
        f"нажми на кнопку ниже."
    )

    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=kb
    )

@bot.message_handler(commands=['SendAdminMessage'])
def send_admin_message(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ У тебя нет доступа к этой команде.")
        return

    # Проверяем, есть ли текст после команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "⚠ Использование:\n/SendAdminMessage текст сообщения")
        return

    admin_text = args[1]

    # Выделенное сообщение
    formatted_text = (
        "🔥 <b>Сообщение от администрации</b>\n\n"
        f"{admin_text}"
    )

    bot.send_message(
        CHANNEL_ID,
        formatted_text,
        parse_mode="HTML"
    )

    bot.reply_to(message, "✅ Сообщение отправлено в канал без модерации.")

# ===== ОБРАБОТКА КНОПОК =====
@bot.callback_query_handler(func=lambda c: c.data in ["write_msg", "send_more"])
def ask_message(call):
    can_send, time_left = check_cooldown(call.from_user.id)
    if not can_send:
        bot.answer_callback_query(
            call.id,
            f"⏳ Подожди {time_left} перед следующей отправкой",
            show_alert=True
        )
        return

    waiting_for_message.add(call.from_user.id)
    bot.send_message(call.message.chat.id, "✍️ Пришли то, что хочешь опубликовать и жди решения модерации.")
    bot.answer_callback_query(call.id)

# ===== ПРИЕМ КОНТЕНТА ОТ ПОЛЬЗОВАТЕЛЯ =====
@bot.message_handler(content_types=['text', 'photo', 'video', 'voice', 'document'])
def forward_to_admin(message):
    if message.from_user.id not in waiting_for_message:
        return

    # Снимаем статус ожидания и ставим кулдаун
    waiting_for_message.remove(message.from_user.id)
    user_cooldowns[message.from_user.id] = time.time()

    user = message.from_user
    admin_kb = types.InlineKeyboardMarkup()
    admin_kb.add(
        types.InlineKeyboardButton("✅ Принять", callback_data="accept"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data="reject")
    )

    info_text = (
        f"📩 Новое сообщение в предложку\n"
        f"👤 От: {user.first_name} (@{user.username})\n"
        f"🆔 ID: {user.id}"
    )

    # Пересылка админу
    try:
        if message.content_type == "text":
            sent = bot.send_message(
                ADMIN_ID,
                f"{info_text}\n\nТекст:\n{message.text}",
                reply_markup=admin_kb
            )
        else:
            bot.send_message(ADMIN_ID, info_text)
            sent = bot.copy_message(
                ADMIN_ID,
                message.chat.id,
                message.message_id,
                reply_markup=admin_kb
            )

        # Сохраняем данные сообщения для админа
        admin_messages[sent.message_id] = (user.id, message)

        user_kb = types.InlineKeyboardMarkup()
        user_kb.add(types.InlineKeyboardButton("➕ Отправить ещё", callback_data="send_more"))

        bot.send_message(
            message.chat.id,
            "✅ Твое сообщение отправлено на модерацию!",
            reply_markup=user_kb
        )
        logger.info(f"Сообщение от {user.id} отправлено админу")
    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")
        bot.send_message(message.chat.id, "❌ Произошла ошибка. Попробуй позже.")

# ===== РЕШЕНИЕ АДМИНА =====
@bot.callback_query_handler(func=lambda c: c.data in ["accept", "reject"])
def admin_decision(call):
    if call.from_user.id != ADMIN_ID:
        return

    data = admin_messages.get(call.message.message_id)
    if not data:
        bot.answer_callback_query(call.id, "Ошибка: данные не найдены")
        return

    user_id, original_message = data

    try:
        if call.data == "accept":
            # Публикация в канал
            if original_message.content_type == "text":
                bot.send_message(CHANNEL_ID, original_message.text)
            else:
                bot.copy_message(
                    CHANNEL_ID,
                    original_message.chat.id,
                    original_message.message_id
                )

            bot.send_message(user_id, "✅ Твое сообщение опубликовано в канале!")
            status = "✅ Опубликовано"
            logger.info(f"Сообщение от {user_id} опубликовано")
        else:
            bot.send_message(user_id, "❌ К сожалению, твое сообщение отклонено модератором.")
            status = "❌ Отклонено"
            logger.info(f"Сообщение от {user_id} отклонено")

        # Убираем кнопки у админа после решения
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )
        
        # Удаляем из памяти
        del admin_messages[call.message.message_id]
        
    except Exception as e:
        logger.error(f"Ошибка при решении админа: {e}")
        status = "❌ Ошибка"
    
    bot.answer_callback_query(call.id, status)

# ===== ФУНКЦИЯ ДЛЯ ПОДДЕРЖАНИЯ РАБОТЫ (для Render/Replit) =====
def start_web_server():
    """Запускает простой веб-сервер для пингов"""
    try:
        from flask import Flask
        app = Flask(__name__)
        
        @app.route('/')
        def home():
            return "✅ Бот работает!"
        
        @app.route('/ping')
        def ping():
            return "pong"
        
        # Render дает порт через переменную окружения
        port = int(os.environ.get('PORT', 8080))
        app.run(host='0.0.0.0', port=port)
        logger.info(f"Веб-сервер запущен на порту {port}")
    except Exception as e:
        logger.error(f"Веб-сервер не запущен: {e}")

# ===== ЗАПУСК =====
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("ЗАПУСК БОТА")
    logger.info("=" * 50)
    
    # Запускаем веб-сервер в отдельном потоке (для Render/Replit)
    web_thread = Thread(target=start_web_server)
    web_thread.daemon = True
    web_thread.start()
    logger.info("Веб-сервер запущен в фоне")
    
    # Запускаем бота
    logger.info("Бот запускается...")
    try:
        bot.polling(none_stop=True, interval=1, timeout=30)
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
        time.sleep(5)
        # Пробуем перезапустить
        logger.info("Перезапуск бота через 5 секунд...")
        time.sleep(5)
        os.execl(sys.executable, sys.executable, *sys.argv)