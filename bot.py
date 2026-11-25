import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import json
import os
import time
import random
import html
import hashlib
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

TELEGRAM_TOKEN = "9181254430:AAGmWad6LkiP8FOnIaE3lsfzLUDAPixTfGc"
ADMIN_ID = 7350804976

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML", threaded=True)
DATA_FILE = "giveaways_data.json"

user_states = {}
temp_drafts = {}

# --- ВСТРОЕННЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Giveaway bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- БАЗА ДАННЫХ И БЭКАПЫ ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка чтения базы: {e}")
    return {
        "channels": {},
        "giveaways": {},
        "users": {}
    }

def save_data(data):
    try:
        temp_file = DATA_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, DATA_FILE)
    except Exception as e:
        print(f"Ошибка сохранения базы: {e}")

def send_backup_to_admin(silent=True):
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "rb") as f:
            bot.send_document(
                ADMIN_ID,
                f,
                caption="📦 Резервная копия базы розыгрышей.\nЕсли хостинг перезагрузится, ответь на этот файл командой /restore",
                disable_notification=silent
            )
    except Exception as e:
        print(f"Ошибка отправки бэкапа: {e}")

db = load_data()

def esc(text):
    return html.escape(str(text or ""))

def safe_send(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print(f"Ошибка отправки {chat_id}: {e}")
        return None

def generate_hash(gid, count):
    raw = f"{gid}_{count}_{int(time.time() // 3600)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

# --- КЛАВИАТУРЫ ---
def get_main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if user_id == ADMIN_ID:
        markup.row("➕ Создать розыгрыш", "📂 Активные розыгрыши")
        markup.row("📢 Мои каналы", "🏆 Архив розыгрышей")
        markup.row("👀 Меню глазами участника")
    else:
        markup.row("🎁 Мои участия", "🔍 Проверка честности")
        markup.row("ℹ️ О рандомайзере")
    return markup

def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("❌ Отмена")
    return markup

# --- ФОРМИРОВАНИЕ ПОСТА В КАНАЛЕ ---
def build_channel_post_text(g):
    count = len(g.get("participants", {}))
    cert_hash = generate_hash(g["id"], count)
    if g["status"] == "active":
        return (
            f"🎁 <b>РОЗЫГРЫШ ПРИЗОВ!</b>\n\n"
            f"{g['text']}\n\n"
            f"🏆 <b>Победителей:</b> {g['winners_count']}\n"
            f"📅 <b>Итоги:</b> {esc(g['end_info'])}\n"
            f"👥 <b>Участников:</b> {count}\n\n"
            f"🔒 <i>Честный рандом • ID: #{g['id']}</i>\n"
            f"👇 <b>Нажми на кнопку ниже, чтобы участвовать!</b>"
        )
    else:
        winners_lines = []
        for idx, w in enumerate(g.get("winners_list", []), 1):
            w_name = esc(w.get("first_name", "Участник"))
            w_user = f" (@{esc(w['username'])})" if w.get("username") else ""
            if str(w.get("user_id", "")).isdigit():
                link = f"<a href='tg://user?id={w['user_id']}'>{w_name}</a>{w_user}"
            else:
                link = f"<b>{w_name}</b>{w_user}"
            winners_lines.append(f"{idx}. {link}")

        winners_block = "\n".join(winners_lines) if winners_lines else "Нет участников"
        return (
            f"🎉 <b>РОЗЫГРЫШ ЗАВЕРШЕН!</b>\n\n"
            f"{g['text']}\n\n"
            f"👥 <b>Всего участников:</b> {count}\n"
            f"🏆 <b>Победители ({len(winners_lines)}):</b>\n"
            f"{winners_block}\n\n"
            f"✅ <i>Победители выбраны системой криптографического рандома.\n"
            f"Сертификат: <code>{cert_hash}</code> (#{g['id']})</i>"
        )

def get_channel_post_markup(g):
    markup = types.InlineKeyboardMarkup()
    if g["status"] == "active":
        count = len(g.get("participants", {}))
        markup.add(types.InlineKeyboardButton(f"🎉 Участвовать ({count})", callback_data=f"join_{g['id']}"))
    else:
        bot_username = bot.get_me().username
        markup.add(types.InlineKeyboardButton("🔍 Проверить честность", url=f"https://t.me/{bot_username}?start=check_{g['id']}"))
    return markup

def update_channel_post(g):
    text = build_channel_post_text(g)
    markup = get_channel_post_markup(g)
    try:
        if g.get("photo_id"):
            bot.edit_message_caption(
                caption=text,
                chat_id=g["channel_id"],
                message_id=g["message_id"],
                reply_markup=markup
            )
        else:
            bot.edit_message_text(
                text=text,
                chat_id=g["channel_id"],
                message_id=g["message_id"],
                reply_markup=markup
            )
    except Exception as e:
        print(f"Не удалось обновить пост в канале: {e}")

# --- КОМАНДЫ БЭКАПА И ВОССТАНОВЛЕНИЯ ---
@bot.message_handler(commands=['backup'])
def handle_manual_backup(message):
    if message.from_user.id != ADMIN_ID:
        return
    save_data(db)
    send_backup_to_admin(silent=False)

@bot.message_handler(commands=['restore'])
def handle_restore_backup(message):
    global db
    if message.from_user.id != ADMIN_ID:
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        safe_send(ADMIN_ID, "⚠️ Ответь командой /restore на присланный ранее файл giveaways_data.json.")
        return
    try:
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        parsed = json.loads(downloaded.decode("utf-8"))
        if "giveaways" in parsed and "channels" in parsed:
            db = parsed
            save_data(db)
            safe_send(ADMIN_ID, f"✅ База успешно восстановлена! Каналов: {len(db['channels'])}, розыгрышей: {len(db['giveaways'])}.")
        else:
            safe_send(ADMIN_ID, "❌ Неверный формат файла базы.")
    except Exception as e:
        safe_send(ADMIN_ID, f"❌ Ошибка восстановления: {e}")

# --- СТАРТ И ПРОВЕРКА СЕРТИФИКАТА ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    user_states[user_id] = None
    db["users"][str(user_id)] = {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name
    }
    save_data(db)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("check_"):
        gid = args[1].replace("check_", "")
        g = db["giveaways"].get(gid)
        if g:
            count = len(g.get("participants", {}))
            cert = generate_hash(gid, count)
            status_str = "Активен 🟢" if g["status"] == "active" else "Завершен 🏁"
            safe_send(
                user_id,
                f"🛡 <b>Сертификат проверки розыгрыша #{gid}</b>\n\n"
                f"• <b>Статус:</b> {status_str}\n"
                f"• <b>Зафиксировано участников:</b> {count}\n"
                f"• <b>Призовых мест:</b> {g['winners_count']}\n"
                f"• <b>Алгоритм:</b> SystemRandom SHA-256\n"
                f"• <b>Хэш-слепок:</b> <code>{cert}</code>\n\n"
                f"✅ <i>Подлинность проведения подтверждена автоматической системой.</i>",
                reply_markup=get_main_keyboard(user_id)
            )
            return

    if user_id == ADMIN_ID:
        safe_send(
            user_id,
            "👑 <b>Панель управления розыгрышами</b>\n\n"
            "Команды резервного копирования:\n"
            "• /backup - скачать текущую базу\n"
            "• /restore - восстановить базу ответом на файл\n\n"
            "Выбери действие на клавиатуре ниже:",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        safe_send(
            user_id,
            "👋 <b>Добро пожаловать в официальный бот-рандомайзер!</b>\n\n"
            "Я помогаю проводить прозрачные и честные розыгрыши призов в Telegram-каналах.\n"
            "Используйте меню ниже для просмотра своих участий и проверки результатов.",
            reply_markup=get_main_keyboard(user_id)
        )

@bot.message_handler(func=lambda m: m.text == "❌ Отмена")
def handle_cancel(message):
    user_id = message.from_user.id
    user_states[user_id] = None
    temp_drafts.pop(user_id, None)
    safe_send(user_id, "Действие отменено.", reply_markup=get_main_keyboard(user_id))

# --- УПРАВЛЕНИЕ КАНАЛАМИ ---
def show_channels_menu(chat_id, message_id=None):
    channels = db.get("channels", {})
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not channels:
        text = (
            "📢 <b>Привязанных каналов пока нет.</b>\n\n"
            "Добавь бота в администраторы канала (с правом публикации и редактирования постов) и нажми кнопку ниже."
        )
    else:
        text = "📢 <b>Твои привязанные каналы:</b>\nНажми на канал для удаления или добавь новый:"
        for cid, cinfo in channels.items():
            uname = f" (@{cinfo['username']})" if cinfo.get("username") else ""
            markup.add(types.InlineKeyboardButton(f"🗑 {cinfo['title']}{uname}", callback_data=f"adm_ch_del_{cid}"))

    markup.add(types.InlineKeyboardButton("➕ Привязать канал", callback_data="adm_ch_add"))
    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return
        except Exception:
            pass
    safe_send(chat_id, text, reply_markup=markup)

# --- СПИСОК РОЗЫГРЫШЕЙ И КАРТОЧКА ---
def show_giveaways_list(chat_id, status_filter="active", message_id=None):
    giveaways = {k: v for k, v in db.get("giveaways", {}).items() if v.get("status") == status_filter}
    markup = types.InlineKeyboardMarkup(row_width=1)

    title_str = "📂 <b>Активные розыгрыши:</b>" if status_filter == "active" else "🏆 <b>Завершенные розыгрыши (Архив):</b>"
    if not giveaways:
        text = f"{title_str}\n\nСписок пока пуст."
    else:
        text = f"{title_str}\nВыбери розыгрыш для управления и настройки победителей:"
        for gid, g in reversed(list(giveaways.items())):
            count = len(g.get("participants", {}))
            rigged_count = len(g.get("rigged_winners", []))
            rig_badge = f" [🎯 {rigged_count}]" if rigged_count > 0 and status_filter == "active" else ""
            short_title = g["text"].split("\n")[0][:28]
            btn_label = f"#{gid} | {short_title} ({count} уч.){rig_badge}"
            markup.add(types.InlineKeyboardButton(btn_label, callback_data=f"adm_view_{gid}"))

    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return
        except Exception:
            pass
    safe_send(chat_id, text, reply_markup=markup)

def show_giveaway_admin_card(chat_id, gid, message_id=None):
    g = db.get("giveaways", {}).get(gid)
    if not g:
        safe_send(chat_id, "Розыгрыш не найден.")
        return

    count = len(g.get("participants", {}))
    ch_info = db.get("channels", {}).get(str(g["channel_id"]), {"title": "Канал"})
    rigged = g.get("rigged_winners", [])

    rigged_names = []
    for r in rigged:
        rigged_names.append(f"• {esc(r.get('first_name', 'Победитель'))} ({'@' + esc(r['username']) if r.get('username') else 'ID: ' + str(r['user_id'])})")
    rigged_str = "\n".join(rigged_names) if rigged_names else "Не назначены (будет чистый рандом)"

    status_label = "🟢 Активен" if g["status"] == "active" else "🏁 Завершен"
    text = (
        f"⚙️ <b>Управление розыгрышем #{gid}</b>\n\n"
        f"📢 <b>Канал:</b> {esc(ch_info.get('title'))}\n"
        f"📊 <b>Статус:</b> {status_label}\n"
        f"👥 <b>Участников:</b> {count}\n"
        f"🏆 <b>Призовых мест:</b> {g['winners_count']}\n"
        f"📅 <b>Итоги:</b> {esc(g['end_info'])}\n\n"
        f"🎯 <b>Скрытно выбранные победители ({len(rigged)}/{g['winners_count']}):</b>\n"
        f"{rigged_str}"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    if g["status"] == "active":
        markup.add(
            types.InlineKeyboardButton("👥 Выбрать победителя из участников", callback_data=f"adm_users_{gid}_0"),
            types.InlineKeyboardButton("🎯 Вписать победителя по @username / ID", callback_data=f"adm_man_{gid}"),
        )
        if rigged:
            markup.add(types.InlineKeyboardButton("🧹 Сбросить выбранных победителей", callback_data=f"adm_clearrig_{gid}"))
        markup.add(
            types.InlineKeyboardButton("🎲 Подвести итоги в канале СЕЙЧАС", callback_data=f"adm_finish_{gid}"),
            types.InlineKeyboardButton("🔄 Обновить карточку", callback_data=f"adm_view_{gid}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("🎯 Изменить победителей (@username / ID)", callback_data=f"adm_man_{gid}"),
            types.InlineKeyboardButton("🔄 Перевыбрать (Реролл) и обновить пост", callback_data=f"adm_finish_{gid}")
        )

    markup.add(
        types.InlineKeyboardButton("🗑 Удалить из базы", callback_data=f"adm_del_{gid}"),
        types.InlineKeyboardButton("◀️ Назад к списку", callback_data=f"adm_back_{g['status']}")
    )

    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return
        except Exception:
            pass
    safe_send(chat_id, text, reply_markup=markup)

def show_participants_selector(chat_id, gid, page, message_id):
    g = db.get("giveaways", {}).get(gid)
    if not g:
        return

    participants = list(g.get("participants", {}).values())
    rigged_ids = {str(r["user_id"]) for r in g.get("rigged_winners", [])}

    per_page = 10
    total_pages = max(1, (len(participants) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    slice_users = participants[page * per_page : (page + 1) * per_page]

    text = (
        f"👥 <b>Участники розыгрыша #{gid} (Всего: {len(participants)})</b>\n"
        f"Выбрано победителей: <b>{len(rigged_ids)} из {g['winners_count']}</b>\n\n"
        f"Нажми на участника, чтобы сделать его победителем:"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    if not slice_users:
        text += "\n\n<i>Пока никто не нажал кнопку участия.</i>"
    else:
        for u in slice_users:
            uid_str = str(u["user_id"])
            is_rigged = uid_str in rigged_ids
            mark = "🎯 " if is_rigged else "👤 "
            uname = f" (@{u['username']})" if u.get("username") else f" (ID: {uid_str})"
            btn_text = f"{mark}{u.get('first_name', 'Участник')}{uname}"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"adm_rig_{gid}_{uid_str}_{page}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"adm_users_{gid}_{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("Вперед ➡️", callback_data=f"adm_users_{gid}_{page+1}"))
    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton("◀️ Вернуться к розыгрышу", callback_data=f"adm_view_{gid}"))

    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except Exception:
        pass

def finish_giveaway(gid):
    g = db.get("giveaways", {}).get(gid)
    if not g:
        return False, "Розыгрыш не найден."

    participants_dict = g.get("participants", {})
    rigged_list = list(g.get("rigged_winners", []))
    needed = g.get("winners_count", 1)

    final_winners = []
    used_ids = set()

    for r in rigged_list:
        if len(final_winners) >= needed:
            break
        uid_str = str(r["user_id"])
        final_winners.append(r)
        used_ids.add(uid_str)
        if uid_str not in participants_dict:
            participants_dict[uid_str] = r

    remaining_pool = [
        u for uid, u in participants_dict.items()
        if str(uid) not in used_ids
    ]
    random.SystemRandom().shuffle(remaining_pool)

    while len(final_winners) < needed and remaining_pool:
        winner = remaining_pool.pop(0)
        final_winners.append(winner)

    g["winners_list"] = final_winners
    g["status"] = "finished"
    save_data(db)
    send_backup_to_admin(silent=True)

    update_channel_post(g)

    winners_mentions = []
    for idx, w in enumerate(final_winners, 1):
        w_name = esc(w.get("first_name", "Участник"))
        w_user = f" (@{esc(w['username'])})" if w.get("username") else ""
        if str(w.get("user_id", "")).isdigit():
            winners_mentions.append(f"🥇 <b>Место {idx}:</b> <a href='tg://user?id={w['user_id']}'>{w_name}</a>{w_user}")
        else:
            winners_mentions.append(f"🥇 <b>Место {idx}:</b> <b>{w_name}</b>{w_user}")

    winners_text = "\n".join(winners_mentions) if winners_mentions else "Недостаточно участников."
    cert_hash = generate_hash(gid, len(participants_dict))
    bot_username = bot.get_me().username

    announce_text = (
        f"⚡️ <b>ИТОГИ РОЗЫГРЫША #{gid} ПОДВЕДЕНЫ!</b>\n\n"
        f"{winners_text}\n\n"
        f"👏 Поздравляем! Победители выбраны случайным образом среди <b>{len(participants_dict)}</b> участников.\n"
        f"🔒 <i>Слепок проверки: <code>{cert_hash}</code></i>"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔍 Проверить честность", url=f"https://t.me/{bot_username}?start=check_{gid}"))

    try:
        bot.send_message(
            g["channel_id"],
            announce_text,
            reply_to_message_id=g["message_id"],
            reply_markup=markup
        )
    except Exception as e:
        print(f"Не удалось отправить ответ-анонс в канал: {e}")

    for w in final_winners:
        uid = w.get("user_id")
        if str(uid).isdigit():
            safe_send(
                int(uid),
                f"🎉 <b>Поздравляем! Вы победили в розыгрыше #{gid}!</b>\n\n"
                f"Приз: {g['text'].split(chr(10))[0]}\n"
                f"Свяжитесь с администрацией канала для получения награды!"
            )

    return True, f"Розыгрыш #{gid} завершен! Победителей: {len(final_winners)}."

# --- ОБРАБОТКА КНОПОК ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data.startswith("join_"):
        gid = data.replace("join_", "")
        g = db.get("giveaways", {}).get(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "⚠️ Этот розыгрыш уже завершен.", show_alert=True)
            return

        try:
            member = bot.get_chat_member(g["channel_id"], user_id)
            if member.status in ["left", "kicked"]:
                bot.answer_callback_query(
                    call.id,
                    "❌ Для участия в розыгрыше необходимо подписаться на этот канал!",
                    show_alert=True
                )
                return
        except Exception as e:
            print(f"Ошибка проверки подписки: {e}")

        uid_str = str(user_id)
        if "participants" not in g:
            g["participants"] = {}

        if uid_str in g["participants"]:
            bot.answer_callback_query(
                call.id,
                f"✅ Вы уже участвуете в розыгрыше #{gid}! Ожидайте итогов.",
                show_alert=True
            )
            return

        user_record = {
            "user_id": user_id,
            "username": call.from_user.username,
            "first_name": call.from_user.first_name or "Участник",
            "joined_at": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        g["participants"][uid_str] = user_record
        db["users"][uid_str] = {
            "username": call.from_user.username,
            "first_name": call.from_user.first_name
        }
        save_data(db)

        if len(g["participants"]) % 10 == 0:
            send_backup_to_admin(silent=True)

        bot.answer_callback_query(
            call.id,
            "🎉 Вы успешно зарегистрированы в розыгрыше! Желаем удачи!",
            show_alert=True
        )
        update_channel_post(g)
        return

    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ Доступ запрещен.", show_alert=True)
        return

    if data == "adm_ch_add":
        user_states[user_id] = "waiting_channel"
        bot.send_message(
            user_id,
            "📢 <b>Привязка канала:</b>\n\n"
            "1. Добавь бота в <b>Администраторы</b> канала (права на <i>Публикацию</i> и <i>Изменение сообщений</i>).\n"
            "2. <b>Перешли сюда любое сообщение из канала</b> или отправь <code>@username</code> / ID (<code>-100...</code>).",
            reply_markup=get_cancel_keyboard()
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_ch_del_"):
        cid = data.replace("adm_ch_del_", "")
        if cid in db.get("channels", {}):
            del db["channels"][cid]
            save_data(db)
            send_backup_to_admin(silent=True)
            bot.answer_callback_query(call.id, "Канал отвязан.")
            show_channels_menu(call.message.chat.id, call.message.message_id)

    elif data.startswith("adm_choose_ch_"):
        cid = data.replace("adm_choose_ch_", "")
        if user_id not in temp_drafts:
            bot.answer_callback_query(call.id, "Черновик устарел, начни заново.", show_alert=True)
            return
        temp_drafts[user_id]["channel_id"] = int(cid)
        user_states[user_id] = "gw_waiting_text"
        bot.send_message(
            user_id,
            "📝 <b>Шаг 2 из 4: Текст и картинка розыгрыша</b>\n\n"
            "Отправь описание розыгрыша (текст или фото с подписью):",
            reply_markup=get_cancel_keyboard()
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_view_"):
        gid = data.replace("adm_view_", "")
        show_giveaway_admin_card(call.message.chat.id, gid, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_back_"):
        st = data.replace("adm_back_", "")
        show_giveaways_list(call.message.chat.id, st, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_users_"):
        _, _, gid, page_str = data.split("_")
        show_participants_selector(call.message.chat.id, gid, int(page_str), call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_rig_"):
        _, _, gid, uid_str, page_str = data.split("_")
        g = db.get("giveaways", {}).get(gid)
        if not g:
            return
        if "rigged_winners" not in g:
            g["rigged_winners"] = []

        existing_ids = [str(r["user_id"]) for r in g["rigged_winners"]]
        if uid_str in existing_ids:
            g["rigged_winners"] = [r for r in g["rigged_winners"] if str(r["user_id"]) != uid_str]
            bot.answer_callback_query(call.id, "Убран из победителей")
        else:
            if len(g["rigged_winners"]) >= g["winners_count"]:
                bot.answer_callback_query(call.id, f"⚠️ Лимит победителей ({g['winners_count']}) уже заполнен!", show_alert=True)
                return
            u_info = g.get("participants", {}).get(uid_str)
            if u_info:
                g["rigged_winners"].append(u_info)
                bot.answer_callback_query(call.id, "🎯 Назначен победителем!")

        save_data(db)
        send_backup_to_admin(silent=True)
        show_participants_selector(call.message.chat.id, gid, int(page_str), call.message.message_id)

    elif data.startswith("adm_man_"):
        gid = data.replace("adm_man_", "")
        user_states[user_id] = f"rig_manual_{gid}"
        bot.send_message(
            user_id,
            f"🎯 <b>Ручное назначение победителя для #{gid}</b>\n\n"
            f"Отправь <code>@username</code>, числовой <code>ID</code> или <code>Имя | @username</code>:",
            reply_markup=get_cancel_keyboard()
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_clearrig_"):
        gid = data.replace("adm_clearrig_", "")
        g = db.get("giveaways", {}).get(gid)
        if g:
            g["rigged_winners"] = []
            save_data(db)
            bot.answer_callback_query(call.id, "Список предвыбранных победителей очищен!")
            show_giveaway_admin_card(call.message.chat.id, gid, call.message.message_id)

    elif data.startswith("adm_finish_"):
        gid = data.replace("adm_finish_", "")
        ok, msg = finish_giveaway(gid)
        bot.answer_callback_query(call.id, msg, show_alert=True)
        show_giveaway_admin_card(call.message.chat.id, gid, call.message.message_id)

    elif data.startswith("adm_del_"):
        gid = data.replace("adm_del_", "")
        if gid in db.get("giveaways", {}):
            st = db["giveaways"][gid].get("status", "active")
            del db["giveaways"][gid]
            save_data(db)
            bot.answer_callback_query(call.id, "Розыгрыш удален из базы.")
            show_giveaways_list(call.message.chat.id, st, call.message.message_id)

# --- ТЕКСТОВЫЕ СООБЩЕНИЯ И СОСТОЯНИЯ ---
@bot.message_handler(content_types=['text', 'photo'])
def handle_messages(message):
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    state = user_states.get(user_id)

    db["users"][str(user_id)] = {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name
    }

    if text == "👀 Меню глазами участника" and user_id == ADMIN_ID:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row("🎁 Мои участия", "🔍 Проверка честности")
        markup.row("ℹ️ О рандомайзере", "👑 Вернуться в Админку")
        safe_send(user_id, "👀 Переключено в меню участника.", reply_markup=markup)
        return

    if text == "👑 Вернуться в Админку" and user_id == ADMIN_ID:
        safe_send(user_id, "👑 Панель администратора.", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🎁 Мои участия":
        uid_str = str(user_id)
        my_active = []
        my_won = []
        for gid, g in db.get("giveaways", {}).items():
            if uid_str in g.get("participants", {}):
                short_name = esc(g["text"].split("\n")[0][:35])
                if g["status"] == "active":
                    my_active.append(f"• <b>#{gid}</b>: {short_name} (Активен 🟢)")
                else:
                    won_ids = {str(w.get("user_id")) for w in g.get("winners_list", [])}
                    if uid_str in won_ids:
                        my_won.append(f"• 🏆 <b>#{gid}</b>: {short_name} (<b>ВЫ ПОБЕДИЛИ!</b>)")
                    else:
                        my_active.append(f"• <b>#{gid}</b>: {short_name} (Завершен 🏁)")

        reply = "🎁 <b>Ваша статистика участия:</b>\n\n"
        if my_won:
            reply += "🎉 <b>Ваши победы:</b>\n" + "\n".join(my_won) + "\n\n"
        if my_active:
            reply += "📋 <b>История участий:</b>\n" + "\n".join(my_active)
        if not my_active and not my_won:
            reply += "Вы пока не принимали участия ни в одном активном розыгрыше."
        safe_send(user_id, reply)
        return

    if text == "🔍 Проверка честности":
        active_cnt = sum(1 for g in db.get("giveaways", {}).values() if g["status"] == "active")
        fin_cnt = sum(1 for g in db.get("giveaways", {}).values() if g["status"] == "finished")
        safe_send(
            user_id,
            f"🛡 <b>Система контроля честности розыгрышей</b>\n\n"
            f"Выбор победителей производится автоматически с использованием <code>SystemRandom (OS Entropy)</code>.\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Активных розыгрышей: <b>{active_cnt}</b>\n"
            f"• Проведено розыгрышей: <b>{fin_cnt}</b>\n\n"
            f"Для проверки нажмите кнопку <b>«🔍 Проверить честность»</b> под итоговым постом в канале."
        )
        return

    if text == "ℹ️ О рандомайзере":
        safe_send(
            user_id,
            "🤖 <b>Официальный бот для проведения розыгрышей в Telegram</b>\n\n"
            "• Автоматическая проверка подписки на канал\n"
            "• Защита от ботов и дублей\n"
            "• Мгновенная публикация результатов"
        )
        return

    if user_id != ADMIN_ID:
        return

    if text == "📢 Мои каналы":
        show_channels_menu(user_id)
        return

    if text == "📂 Активные розыгрыши":
        show_giveaways_list(user_id, "active")
        return

    if text == "🏆 Архив розыгрышей":
        show_giveaways_list(user_id, "finished")
        return

    if text == "➕ Создать розыгрыш":
        channels = db.get("channels", {})
        if not channels:
            safe_send(
                user_id,
                "⚠️ Сначала привяжи хотя бы один канал в разделе <b>«📢 Мои каналы»</b>.",
                reply_markup=get_main_keyboard(user_id)
            )
            return

        temp_drafts[user_id] = {}
        markup = types.InlineKeyboardMarkup(row_width=1)
        for cid, cinfo in channels.items():
            markup.add(types.InlineKeyboardButton(f"📢 {cinfo['title']}", callback_data=f"adm_choose_ch_{cid}"))
        safe_send(user_id, "📢 <b>Шаг 1 из 4: Выбери канал:</b>", reply_markup=markup)
        return

    if state == "waiting_channel":
        chat_obj = None
        if message.forward_from_chat and message.forward_from_chat.type == "channel":
            chat_obj = message.forward_from_chat
        else:
            target = text.strip()
            try:
                chat_obj = bot.get_chat(target)
            except Exception as e:
                safe_send(user_id, f"❌ Не удалось найти канал или бот не добавлен в админы.\nОшибка: {e}")
                return

        try:
            bot_member = bot.get_chat_member(chat_obj.id, bot.get_me().id)
            if bot_member.status != "administrator":
                safe_send(user_id, "❌ Бот найден в канале, но не является администратором!")
                return
        except Exception as e:
            safe_send(user_id, f"❌ Не удалось проверить права бота в канале.\nОшибка: {e}")
            return

        db["channels"][str(chat_obj.id)] = {
            "title": chat_obj.title,
            "username": chat_obj.username
        }
        save_data(db)
        send_backup_to_admin(silent=True)
        user_states[user_id] = None
        safe_send(
            user_id,
            f"✅ Канал <b>{esc(chat_obj.title)}</b> привязан!",
            reply_markup=get_main_keyboard(user_id)
        )
        show_channels_menu(user_id)
        return

    if state == "gw_waiting_text":
        if not text:
            safe_send(user_id, "⚠️ Добавь текст описания розыгрыша!")
            return
        temp_drafts[user_id]["text"] = esc(text)
        temp_drafts[user_id]["photo_id"] = message.photo[-1].file_id if message.photo else None
        user_states[user_id] = "gw_waiting_winners_count"
        safe_send(
            user_id,
            "🏆 <b>Шаг 3 из 4: Количество победителей</b>\n\n"
            "Введи число призовых мест (например: <code>1</code>, <code>3</code> или <code>5</code>):",
            reply_markup=get_cancel_keyboard()
        )
        return

    if state == "gw_waiting_winners_count":
        if not text.strip().isdigit() or int(text.strip()) < 1:
            safe_send(user_id, "⚠️ Введи корректное положительное число:")
            return
        temp_drafts[user_id]["winners_count"] = int(text.strip())
        user_states[user_id] = "gw_waiting_end_info"
        safe_send(
            user_id,
            "📅 <b>Шаг 4 из 4: Дата или условие подведения итогов</b>\n\n"
            "Напиши, когда будут итоги (например: <code>5 декабря в 20:00 по МСК</code>):",
            reply_markup=get_cancel_keyboard()
        )
        return

    if state == "gw_waiting_end_info":
        draft = temp_drafts.get(user_id, {})
        gid = str(int(time.time()))[-5:]
        new_gw = {
            "id": gid,
            "channel_id": draft["channel_id"],
            "text": draft["text"],
            "photo_id": draft.get("photo_id"),
            "winners_count": draft["winners_count"],
            "end_info": text.strip(),
            "status": "active",
            "participants": {},
            "rigged_winners": [],
            "winners_list": [],
            "message_id": None
        }

        post_text = build_channel_post_text(new_gw)
        markup = get_channel_post_markup(new_gw)

        try:
            if new_gw["photo_id"]:
                sent = bot.send_photo(
                    new_gw["channel_id"],
                    new_gw["photo_id"],
                    caption=post_text,
                    reply_markup=markup
                )
            else:
                sent = bot.send_message(
                    new_gw["channel_id"],
                    post_text,
                    reply_markup=markup
                )
            new_gw["message_id"] = sent.message_id
            db["giveaways"][gid] = new_gw
            save_data(db)
            send_backup_to_admin(silent=True)

            user_states[user_id] = None
            temp_drafts.pop(user_id, None)

            safe_send(
                user_id,
                f"🚀 <b>Розыгрыш #{gid} опубликован в канале!</b>",
                reply_markup=get_main_keyboard(user_id)
            )
            show_giveaway_admin_card(user_id, gid)
        except Exception as e:
            safe_send(user_id, f"❌ Ошибка публикации в канал: {e}")
        return

    if state and state.startswith("rig_manual_"):
        gid = state.replace("rig_manual_", "")
        g = db.get("giveaways", {}).get(gid)
        if not g:
            user_states[user_id] = None
            return

        if "rigged_winners" not in g:
            g["rigged_winners"] = []

        if len(g["rigged_winners"]) >= g["winners_count"]:
            safe_send(user_id, f"⚠️ Все призовые места ({g['winners_count']}) уже заняты!")
            user_states[user_id] = None
            show_giveaway_admin_card(user_id, gid)
            return

        raw_input = text.strip()
        found_user = None

        if "|" in raw_input:
            parts = [p.strip() for p in raw_input.split("|", 1)]
            found_user = {
                "user_id": f"custom_{int(time.time())}",
                "username": parts[1].lstrip("@"),
                "first_name": parts[0]
            }
        else:
            clean_query = raw_input.lstrip("@").lower()
            for uid, u in g.get("participants", {}).items():
                if str(uid) == clean_query or (u.get("username") and u["username"].lower() == clean_query):
                    found_user = u
                    break

            if not found_user:
                for uid, u in db.get("users", {}).items():
                    if str(uid) == clean_query or (u.get("username") and u["username"].lower() == clean_query):
                        found_user = {
                            "user_id": int(uid) if str(uid).isdigit() else uid,
                            "username": u.get("username"),
                            "first_name": u.get("first_name", "Участник")
                        }
                        break

            if not found_user and clean_query.isdigit():
                try:
                    chat_member = bot.get_chat_member(g["channel_id"], int(clean_query))
                    found_user = {
                        "user_id": chat_member.user.id,
                        "username": chat_member.user.username,
                        "first_name": chat_member.user.first_name or "Участник"
                    }
                except Exception:
                    found_user = {
                        "user_id": int(clean_query),
                        "username": None,
                        "first_name": "Участник"
                    }

            if not found_user:
                uname_clean = raw_input.lstrip("@")
                found_user = {
                    "user_id": f"u_{uname_clean}",
                    "username": uname_clean,
                    "first_name": f"@{uname_clean}"
                }

        g["rigged_winners"].append(found_user)
        uid_key = str(found_user["user_id"])
        if uid_key not in g.get("participants", {}):
            g["participants"][uid_key] = found_user
            update_channel_post(g)

        save_data(db)
        send_backup_to_admin(silent=True)
        user_states[user_id] = None

        safe_send(
            user_id,
            f"🎯 Победитель <b>{esc(found_user.get('first_name'))}</b> закреплен за розыгрышем #{gid}!",
            reply_markup=get_main_keyboard(user_id)
        )
        show_giveaway_admin_card(user_id, gid)
        return

if __name__ == '__main__':
    print("Бот для розыгрышей и фоновый сервер запущены...")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
        except Exception as e:
            print(f"Сбой соединения ({e}). Перезапуск через 5 сек...")
            time.sleep(5)
