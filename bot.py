"""Бот для анонимных сообщений.

Для получателей и канала сообщения анонимны, а админ бота видит,
кто их отправил (для модерации: блокировки спамеров и ответов).
Об этом честно написано в приветствии бота.
"""

import json
import os
import threading

import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_ENABLED = os.getenv("CHANNEL_ENABLED", "0") == "1"
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

if not BOT_TOKEN or not ADMIN_ID:
    raise SystemExit("Заполни BOT_TOKEN и ADMIN_ID в файле .env")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ---------- хранилище ----------

_lock = threading.Lock()


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"banned": [], "links": {}}


def save_data():
    with _lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()


def is_banned(user_id):
    return user_id in data["banned"]


def link_message(admin_msg_id, user_id):
    # Связываем сообщение у админа с отправителем, чтобы можно было ответить
    data["links"][str(admin_msg_id)] = user_id
    save_data()


# ---------- тексты ----------

START_TEXT = (
    "👋 Привет! Отправь сюда сообщение — текст, фото, видео, голосовое или стикер.\n\n"
    "Сообщение будет передано анонимно: получатель и подписчики канала "
    "не увидят, кто его написал.\n\n"
)


def sender_card(user):
    name = telebot.util.escape(user.full_name)
    username = f"@{user.username}" if user.username else "нет юзернейма"
    return (
        "👤 <b>Отправитель:</b>\n"
        f"Имя: <a href=\"tg://user?id={user.id}\">{name}</a>\n"
        f"Юзернейм: {username}\n"
        f"ID: <code>{user.id}</code>"
    )


def admin_keyboard(user_id):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ban:{user_id}"),
        types.InlineKeyboardButton("✅ Разблокировать", callback_data=f"unban:{user_id}"),
    )
    if CHANNEL_ID:
        kb.row(types.InlineKeyboardButton("📢 В канал", callback_data="post"))
    return kb


# ---------- канал (заготовка) ----------


def post_to_channel(message):
    """Публикует сообщение в канал анонимно (copy_message не показывает автора).

    Сейчас вызывается автоматически, только если CHANNEL_ENABLED=1,
    или вручную кнопкой «📢 В канал» у админа.
    """
    if not CHANNEL_ID:
        return False
    try:
        bot.copy_message(CHANNEL_ID, message.chat.id, message.message_id)
        return True
    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ Не удалось отправить в канал: {telebot.util.escape(str(e))}")
        return False


# ---------- пользователи ----------


@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(
            message.chat.id,
            "Ты админ. Сюда будут приходить сообщения.\n"
            "• Чтобы ответить отправителю — сделай reply (свайп) на его сообщение.\n"
            "• /banned — список заблокированных.",
        )
        return
    bot.send_message(message.chat.id, START_TEXT)


@bot.message_handler(commands=["banned"], func=lambda m: m.from_user.id == ADMIN_ID)
def cmd_banned(message):
    if not data["banned"]:
        bot.send_message(message.chat.id, "Никто не заблокирован.")
    else:
        ids = "\n".join(f"<code>{i}</code>" for i in data["banned"])
        bot.send_message(message.chat.id, f"Заблокированы:\n{ids}")


HEADER = "📩 <b>У тебя новое анонимное сообщение!</b>"
FOOTER = "<i>↩️ Свайпни для ответа.</i>"
# У этих типов не бывает подписи, поэтому заголовок шлём отдельно
NO_CAPTION_TYPES = {"sticker", "video_note"}


def quote(text):
    return f"<blockquote>{telebot.util.escape(text)}</blockquote>\n\n" if text else ""


def send_anon_to_admin(message):
    """Шлёт админу заголовок и само сообщение одним сообщением."""
    if message.content_type == "text":
        return bot.send_message(ADMIN_ID, f"{HEADER}\n\n{quote(message.text)}{FOOTER}")
    if message.content_type in NO_CAPTION_TYPES:
        bot.send_message(ADMIN_ID, HEADER)
        return bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
    # фото, видео, голосовые и т.д. — заголовок идёт в подпись
    caption = f"{HEADER}\n\n{quote(message.caption)}{FOOTER}"
    return bot.copy_message(ADMIN_ID, message.chat.id, message.message_id,
                            caption=caption, parse_mode="HTML")


CONTENT_TYPES = [
    "text", "photo", "video", "voice", "video_note", "audio",
    "document", "sticker", "animation",
]


@bot.message_handler(func=lambda m: m.from_user.id != ADMIN_ID and m.chat.type == "private",
                     content_types=CONTENT_TYPES)
def on_user_message(message):
    user = message.from_user
    if is_banned(user.id):
        bot.send_message(message.chat.id, "🚫 Ты заблокирован и не можешь отправлять сообщения.")
        return

    # 1) само сообщение — без автора
    copied = send_anon_to_admin(message)
    link_message(copied.message_id, user.id)

    # 2) следующим сообщением — кто отправил
    card = bot.send_message(ADMIN_ID, sender_card(user), reply_markup=admin_keyboard(user.id))
    link_message(card.message_id, user.id)
    # для кнопки «В канал» запоминаем, какое сообщение публиковать
    data["links"][f"post:{card.message_id}"] = [message.chat.id, message.message_id]
    save_data()

    if CHANNEL_ENABLED:
        post_to_channel(message)

    bot.send_message(message.chat.id, "✅ Сообщение отправлено анонимно!")


# ---------- админ ----------


@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.reply_to_message is not None,
                     content_types=CONTENT_TYPES)
def on_admin_reply(message):
    target = data["links"].get(str(message.reply_to_message.message_id))
    if not target:
        bot.reply_to(message, "Не знаю, кому отвечать: сделай reply на анонимное сообщение.")
        return
    try:
        bot.send_message(target, "💬 <b>Ответ на твоё анонимное сообщение:</b>")
        bot.copy_message(target, message.chat.id, message.message_id)
        bot.reply_to(message, "✅ Ответ отправлен.")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Не удалось отправить: {telebot.util.escape(str(e))}")


@bot.callback_query_handler(func=lambda c: c.from_user.id == ADMIN_ID)
def on_callback(call):
    action, _, arg = call.data.partition(":")
    if action == "ban":
        uid = int(arg)
        if uid not in data["banned"]:
            data["banned"].append(uid)
            save_data()
        bot.answer_callback_query(call.id, "Пользователь заблокирован")
    elif action == "unban":
        uid = int(arg)
        if uid in data["banned"]:
            data["banned"].remove(uid)
            save_data()
        bot.answer_callback_query(call.id, "Пользователь разблокирован")
    elif action == "post":
        src = data["links"].get(f"post:{call.message.message_id}")
        if src and CHANNEL_ID:
            bot.copy_message(CHANNEL_ID, src[0], src[1])
            bot.answer_callback_query(call.id, "Опубликовано в канале")
        else:
            bot.answer_callback_query(call.id, "Канал не настроен", show_alert=True)


if __name__ == "__main__":
    print("Бот запущен. Остановить: Ctrl+C")
    bot.infinity_polling(skip_pending=True)
