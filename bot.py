import telebot
from telebot import types
import json
import os
import time
import random
import html
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
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
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
                caption="Резервная копия базы. Для восстановления ответь на этот файл командой /restore",
                disable_notification=silent
            )
    except Exception as e:
        print(f"Ошибка отправки бэкапа: {e}")

db = load_data()

def esc(text):
    return html.escape(str(text or ""))

def extract_html_content(message):
    if message.text:
        return getattr(message, "html_text", None) or esc(message.text)
    if message.caption:
        return getattr(message, "html_caption", None) or esc(message.caption)
    return ""

def safe_send(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print(f"Ошибка отправки {chat_id}: {e}")
        return None

# --- КЛАВИАТУРЫ АДМИНА ---
def get_admin_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("Создать розыгрыш", "Активные розыгрыши")
    markup.row("Мои каналы", "Архив розыгрышей")
    return markup

def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отмена")
    return markup

# --- ВИЗИТКА РОЗЫГРЫША В КАНАЛЕ ---
def build_channel_post_text(g):
    if g["status"] == "active":
        return g["text"]

    winners_lines = []
    for idx, w in enumerate(g.get("winners_list", []), 1):
        w_name = esc(w.get("first_name", "Участник"))
        w_user = f" (@{esc(w['username'])})" if w.get("username") else ""
        if str(w.get("user_id", "")).isdigit():
            link = f"<a href='tg://user?id={w['user_id']}'>{w_name}</a>{w_user}"
        else:
            link = f"<b>{w_name}</b>{w_user}"
        winners_lines.append(f"{idx}. {link}")

    winners_block = "\n".join(winners_lines) if winners_lines else "Победители не определены"
    return (
        f"{g['text']}\n\n"
        f"——————————\n"
        f"<b>Итоги розыгрыша</b>\n\n"
        f"{winners_block}"
    )

def get_channel_post_markup(g):
    markup = types.InlineKeyboardMarkup()
    if g["status"] == "active":
        count = len(g.get("participants", {}))
        markup.add(types.InlineKeyboardButton(f"Участвовать ({count})", callback_data=f"join_{g['id']}"))
    else:
        markup.add(types.InlineKeyboardButton("Розыгрыш завершен", callback_data=f"ended_{g['id']}"))
    return markup

def update_channel_post(g):
    text = build_channel_post_text(g)
    markup = get_channel_post_markup(g)
    try:
        if g.get("photo_id") or g.get("video_id"):
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

# --- БЭКАП И ВОССТАНОВЛЕНИЕ ---
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
        safe_send(ADMIN_ID, "Ответь командой /restore на файл giveaways_data.json.")
        return
    try:
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        parsed = json.loads(downloaded.decode("utf-8"))
        if "giveaways" in parsed and "channels" in parsed:
            db = parsed
            save_data(db)
            safe_send(ADMIN_ID, f"База восстановлена. Каналов: {len(db['channels'])}, розыгрышей: {len(db['giveaways'])}.")
        else:
            safe_send(ADMIN_ID, "Неверный формат файла.")
    except Exception as e:
        safe_send(ADMIN_ID, f"Ошибка восстановления: {e}")

# --- СТАРТ (ТОЛЬКО ДЛЯ АДМИНА) ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    user_states[user_id] = None
    safe_send(
        user_id,
        "Панель управления розыгрышами.\n\n"
        "Команды для базы:\n"
        "/backup - скачать файл базы\n"
        "/restore - восстановить базу ответом на файл",
        reply_markup=get_admin_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "Отмена" and m.from_user.id == ADMIN_ID)
def handle_cancel(message):
    user_id = message.from_user.id
    user_states[user_id] = None
    temp_drafts.pop(user_id, None)
    safe_send(user_id, "Действие отменено.", reply_markup=get_admin_keyboard())

# --- УПРАВЛЕНИЕ КАНАЛАМИ ---
def show_channels_menu(chat_id, message_id=None):
    channels = db.get("channels", {})
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not channels:
        text = (
            "Привязанных каналов пока нет.\n\n"
            "Добавь бота в администраторы канала (с правами на публикацию и редактирование сообщений) и нажми кнопку ниже."
        )
    else:
        text = "Привязанные каналы (нажми, чтобы отвязать):"
        for cid, cinfo in channels.items():
            uname = f" (@{cinfo['username']})" if cinfo.get("username") else ""
            markup.add(types.InlineKeyboardButton(f"Удалить: {cinfo['title']}{uname}", callback_data=f"adm_ch_del_{cid}"))

    markup.add(types.InlineKeyboardButton("Привязать канал", callback_data="adm_ch_add"))
    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return
        except Exception:
            pass
    safe_send(chat_id, text, reply_markup=markup)

# --- СПИСОК РОЗЫГРЫШЕЙ И КАРТОЧКА УПРАВЛЕНИЯ ---
def show_giveaways_list(chat_id, status_filter="active", message_id=None):
    giveaways = {k: v for k, v in db.get("giveaways", {}).items() if v.get("status") == status_filter}
    markup = types.InlineKeyboardMarkup(row_width=1)

    title_str = "Активные розыгрыши:" if status_filter == "active" else "Завершенные розыгрыши:"
    if not giveaways:
        text = f"<b>{title_str}</b>\n\nСписок пуст."
    else:
        text = f"<b>{title_str}</b>\nВыбери розыгрыш для настройки победителей:"
        for gid, g in reversed(list(giveaways.items())):
            count = len(g.get("participants", {}))
            rigged_count = len(g.get("rigged_winners", []))
            rig_badge = f" [выбрано: {rigged_count}]" if rigged_count > 0 and status_filter == "active" else ""
            clean_preview = html.unescape(g["text"]).split("\n")[0][:26]
            btn_label = f"#{gid} | {clean_preview} ({count} уч.){rig_badge}"
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
        rigged_names.append(f"- {esc(r.get('first_name', 'Победитель'))} ({'@' + esc(r['username']) if r.get('username') else 'ID: ' + str(r['user_id'])})")
    rigged_str = "\n".join(rigged_names) if rigged_names else "Не назначены (случайный выбор)"

    status_label = "Активен" if g["status"] == "active" else "Завершен"
    text = (
        f"<b>Управление розыгрышем #{gid}</b>\n\n"
        f"Канал: {esc(ch_info.get('title'))}\n"
        f"Статус: {status_label}\n"
        f"Участников: {count}\n"
        f"Призовых мест: {g['winners_count']}\n\n"
        f"<b>Назначенные победители ({len(rigged)}/{g['winners_count']}):</b>\n"
        f"{rigged_str}"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    if g["status"] == "active":
        markup.add(
            types.InlineKeyboardButton("Выбрать из списка участников", callback_data=f"adm_users_{gid}_0"),
            types.InlineKeyboardButton("Вписать победителя по @username / ID", callback_data=f"adm_man_{gid}"),
        )
        if rigged:
            markup.add(types.InlineKeyboardButton("Сбросить выбранных победителей", callback_data=f"adm_clearrig_{gid}"))
        markup.add(
            types.InlineKeyboardButton("Подвести итоги в канале сейчас", callback_data=f"adm_finish_{gid}"),
            types.InlineKeyboardButton("Обновить данные", callback_data=f"adm_view_{gid}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("Изменить победителей (@username / ID)", callback_data=f"adm_man_{gid}"),
            types.InlineKeyboardButton("Перевыбрать (реролл) и обновить пост", callback_data=f"adm_finish_{gid}")
        )

    markup.add(
        types.InlineKeyboardButton("Удалить розыгрыш из базы", callback_data=f"adm_del_{gid}"),
        types.InlineKeyboardButton("Назад к списку", callback_data=f"adm_back_{g['status']}")
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
        f"<b>Участники розыгрыша #{gid} (Всего: {len(participants)})</b>\n"
        f"Назначено победителей: {len(rigged_ids)} из {g['winners_count']}\n\n"
        f"Нажми на участника, чтобы сделать его победителем:"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    if not slice_users:
        text += "\n\nПока никто не нажал кнопку участия."
    else:
        for u in slice_users:
            uid_str = str(u["user_id"])
            is_rigged = uid_str in rigged_ids
            mark = "[Победитель] " if is_rigged else ""
            uname = f" (@{u['username']})" if u.get("username") else f" (ID: {uid_str})"
            btn_text = f"{mark}{u.get('first_name', 'Участник')}{uname}"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"adm_rig_{gid}_{uid_str}_{page}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("Назад", callback_data=f"adm_users_{gid}_{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("Вперед", callback_data=f"adm_users_{gid}_{page+1}"))
    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton("Вернуться к розыгрышу", callback_data=f"adm_view_{gid}"))

    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except Exception:
        pass

# --- ПОДВЕДЕНИЕ ИТОГОВ ---
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
            winners_mentions.append(f"{idx}. <a href='tg://user?id={w['user_id']}'>{w_name}</a>{w_user}")
        else:
            winners_mentions.append(f"{idx}. <b>{w_name}</b>{w_user}")

    winners_text = "\n".join(winners_mentions) if winners_mentions else "Победители не определены."

    announce_text = (
        f"<b>Итоги розыгрыша</b>\n\n"
        f"Победители:\n"
        f"{winners_text}"
    )

    try:
        bot.send_message(
            g["channel_id"],
            announce_text,
            reply_to_message_id=g["message_id"]
        )
    except Exception as e:
        print(f"Не удалось отправить ответ-анонс в канал: {e}")

    return True, f"Розыгрыш #{gid} завершен. Победителей: {len(final_winners)}."

# --- ОБРАБОТКА КНОПОК ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data.startswith("ended_"):
        bot.answer_callback_query(call.id, "Розыгрыш уже завершен.", show_alert=True)
        return

    if data.startswith("join_"):
        gid = data.replace("join_", "")
        g = db.get("giveaways", {}).get(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "Розыгрыш уже завершен.", show_alert=True)
            return

        try:
            member = bot.get_chat_member(g["channel_id"], user_id)
            if member.status in ["left", "kicked"]:
                bot.answer_callback_query(
                    call.id,
                    "Для участия необходимо подписаться на канал.",
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
                "Вы уже участвуете в этом розыгрыше.",
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
            "Вы участвуете в розыгрыше.",
            show_alert=True
        )
        update_channel_post(g)
        return

    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещен.", show_alert=True)
        return

    if data == "adm_ch_add":
        user_states[user_id] = "waiting_channel"
        bot.send_message(
            user_id,
            "Привязка канала:\n\n"
            "1. Добавь бота в администраторы канала (права на публикацию и изменение сообщений).\n"
            "2. Перешли сюда любое сообщение из канала или отправь его @username / ID (-100...).",
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
            "Шаг 2 из 3: Отправь готовый пост для канала (текст, фото с подписью или видео с подписью).\n\n"
            "Бот опубликует его точно в таком виде, как ты напишешь, без лишних слов и смайликов.",
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
                bot.answer_callback_query(call.id, f"Лимит победителей ({g['winners_count']}) уже заполнен.", show_alert=True)
                return
            u_info = g.get("participants", {}).get(uid_str)
            if u_info:
                g["rigged_winners"].append(u_info)
                bot.answer_callback_query(call.id, "Назначен победителем.")

        save_data(db)
        send_backup_to_admin(silent=True)
        show_participants_selector(call.message.chat.id, gid, int(page_str), call.message.message_id)

    elif data.startswith("adm_man_"):
        gid = data.replace("adm_man_", "")
        user_states[user_id] = f"rig_manual_{gid}"
        bot.send_message(
            user_id,
            f"Ручное назначение победителя для #{gid}.\n\n"
            f"Отправь @username, числовой ID или в формате: Имя | @username",
            reply_markup=get_cancel_keyboard()
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("adm_clearrig_"):
        gid = data.replace("adm_clearrig_", "")
        g = db.get("giveaways", {}).get(gid)
        if g:
            g["rigged_winners"] = []
            save_data(db)
            bot.answer_callback_query(call.id, "Список выбранных победителей очищен.")
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
            bot.answer_callback_query(call.id, "Розыгрыш удален.")
            show_giveaways_list(call.message.chat.id, st, call.message.message_id)

# --- ТЕКСТОВЫЕ СООБЩЕНИЯ И СОСТОЯНИЯ (ТОЛЬКО АДМИН) ---
@bot.message_handler(content_types=['text', 'photo', 'video'])
def handle_messages(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    text = message.text or message.caption or ""
    state = user_states.get(user_id)

    if text == "Мои каналы":
        show_channels_menu(user_id)
        return

    if text == "Активные розыгрыши":
        show_giveaways_list(user_id, "active")
        return

    if text == "Архив розыгрышей":
        show_giveaways_list(user_id, "finished")
        return

    if text == "Создать розыгрыш":
        channels = db.get("channels", {})
        if not channels:
            safe_send(
                user_id,
                "Сначала привяжи канал в разделе «Мои каналы».",
                reply_markup=get_admin_keyboard()
            )
            return

        temp_drafts[user_id] = {}
        markup = types.InlineKeyboardMarkup(row_width=1)
        for cid, cinfo in channels.items():
            markup.add(types.InlineKeyboardButton(cinfo['title'], callback_data=f"adm_choose_ch_{cid}"))
        safe_send(user_id, "Шаг 1 из 3: Выбери канал для публикации:", reply_markup=markup)
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
                safe_send(user_id, f"Не удалось найти канал или бот не в админах.\nОшибка: {e}")
                return

        try:
            bot_member = bot.get_chat_member(chat_obj.id, bot.get_me().id)
            if bot_member.status != "administrator":
                safe_send(user_id, "Бот не является администратором этого канала.")
                return
        except Exception as e:
            safe_send(user_id, f"Не удалось проверить права бота.\nОшибка: {e}")
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
            f"Канал <b>{esc(chat_obj.title)}</b> привязан.",
            reply_markup=get_admin_keyboard()
        )
        show_channels_menu(user_id)
        return

    if state == "gw_waiting_text":
        html_body = extract_html_content(message)
        if not html_body:
            safe_send(user_id, "Добавь текст поста.")
            return
        temp_drafts[user_id]["text"] = html_body
        temp_drafts[user_id]["photo_id"] = message.photo[-1].file_id if message.photo else None
        temp_drafts[user_id]["video_id"] = message.video.file_id if message.video else None
        user_states[user_id] = "gw_waiting_winners_count"
        safe_send(
            user_id,
            "Шаг 3 из 3: Введи количество победителей (число, например 1 или 3).\n"
            "В сам пост это число добавляться не будет, оно нужно только для выбора победителей.",
            reply_markup=get_cancel_keyboard()
        )
        return

    if state == "gw_waiting_winners_count":
        if not text.strip().isdigit() or int(text.strip()) < 1:
            safe_send(user_id, "Введи корректное положительное число:")
            return

        draft = temp_drafts.get(user_id, {})
        gid = str(int(time.time()))[-5:]
        new_gw = {
            "id": gid,
            "channel_id": draft["channel_id"],
            "text": draft["text"],
            "photo_id": draft.get("photo_id"),
            "video_id": draft.get("video_id"),
            "winners_count": int(text.strip()),
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
            elif new_gw["video_id"]:
                sent = bot.send_video(
                    new_gw["channel_id"],
                    new_gw["video_id"],
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
                f"Розыгрыш #{gid} опубликован в канале.",
                reply_markup=get_admin_keyboard()
            )
            show_giveaway_admin_card(user_id, gid)
        except Exception as e:
            safe_send(user_id, f"Ошибка публикации в канал: {e}")
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
            safe_send(user_id, f"Все призовые места ({g['winners_count']}) уже заняты.")
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
            f"Победитель <b>{esc(found_user.get('first_name'))}</b> закреплен за розыгрышем #{gid}.",
            reply_markup=get_admin_keyboard()
        )
        show_giveaway_admin_card(user_id, gid)
        return

if __name__ == '__main__':
    print("Бот запущен...")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
        except Exception as e:
            print(f"Сбой соединения ({e}). Перезапуск через 5 сек...")
            time.sleep(5)
