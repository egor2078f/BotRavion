import logging
import re
import asyncio
from datetime import datetime, timedelta

# Проверка наличия библиотеки перед запуском
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, 
        CommandHandler, 
        MessageHandler, 
        CallbackQueryHandler, 
        ContextTypes, 
        filters
    )
except ImportError:
    print("CRITICAL ERROR: Библиотека 'python-telegram-bot' не установлена.")
    print("Убедитесь, что вы загрузили файл requirements.txt и установили зависимости.")
    exit(1)

# Настройка логирования (чтобы видеть ошибки в консоли хостинга)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
# Рекомендуется использовать переменные окружения на хостинге, но для простоты оставил тут
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20" 
CHANNEL = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ADMIN_ID = 7637946765
MODERATOR_ID = 6510703948
ALLOWED_USERS = {ADMIN_ID, MODERATOR_ID}

# Хранилища данных в памяти (сбросятся при перезапуске бота)
user_data = {}
scheduled_posts = {}

# --- ДЕКОРАТОРЫ ---
def check_access(func):
    """Проверка прав доступа (Админ/Модератор)"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = None
        if update.message:
            user_id = update.message.from_user.id
        elif update.callback_query:
            user_id = update.callback_query.from_user.id
        
        if user_id not in ALLOWED_USERS:
            # Можно раскомментировать, если хотите уведомлять о запрете
            # if update.message: await update.message.reply_text("❌ Нет доступа.")
            return
        return await func(update, context)
    return wrapper

# --- ЛОГИКА ОБРАБОТКИ СКРИПТА ---
def process_script_logic(text):
    """
    Находит loadstring и добавляет ватермарку канала.
    Очищает код от лишнего мусора.
    """
    code_lines = []
    in_code_block = False
    
    # Разбиваем текст на строки
    raw_lines = text.split('\n')
    
    for line in raw_lines:
        stripped = line.strip()
        
        # Пропускаем маркеры кода Markdown, если они есть
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
            
        # Логика определения строки кода
        is_code_line = (
            in_code_block or 
            any(k in line.lower() for k in ['loadstring', 'game:', 'local', 'function', 'http', 'script', 'args'])
        )

        if is_code_line:
            # Та самая логика замены loadstring
            if 'loadstring' in stripped and 'game:HttpGet' in stripped:
                # Если заканчивается на (), заменяем на ("ссылка")
                if stripped.endswith('()'):
                    stripped = stripped[:-2] + f'("{WATERMARK_URL}")'
                # Если заканчивается на ();, заменяем на ("ссылка");
                elif stripped.endswith('();'):
                    stripped = stripped[:-3] + f'("{WATERMARK_URL}");'
            
            code_lines.append(stripped)
            
    return code_lines

def format_post(game_name, description, has_key, code):
    """Формирует красивый текст поста"""
    lines = []
    
    lines.append(f"🎮 **{game_name.upper()}**")
    lines.append("")
    
    if description:
        lines.append(f"{description}")
        lines.append("")
    
    key_status = "🔐 **Key:** Required" if has_key else "🔓 **Key:** Not Required"
    lines.append(key_status)
    lines.append("")
    
    if code:
        lines.append("⚡ **Script:**")
        lines.append("```lua")
        lines.extend(code)
        lines.append("```")
    
    lines.append("")
    lines.append(f"💎 **Source:** {CHANNEL}")
    
    return '\n'.join(lines)

def get_channel_button():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Open Channel", url='[https://t.me/RavionScripts](https://t.me/RavionScripts)')
    ]])

def parse_time(time_str):
    """Парсинг времени для отложки"""
    try:
        now = datetime.now()
        time_str = time_str.lower().strip()
        
        # Формат 14:30
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0)
            return target + timedelta(days=1) if target <= now else target
        
        # Формат: завтра 14:30
        if 'завтра' in time_str:
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_match:
                return (now + timedelta(days=1)).replace(hour=int(time_match.group(1)), minute=int(time_match.group(2)), second=0)
                
        # Формат: через 1 час
        hours_match = re.search(r'через\s+(\d+)\s+ч', time_str)
        if hours_match:
            return now + timedelta(hours=int(hours_match.group(1)))
            
        # Формат: через 10 мин
        mins_match = re.search(r'через\s+(\d+)\s+мин', time_str)
        if mins_match:
            return now + timedelta(minutes=int(mins_match.group(1)))
    except Exception as e:
        logger.error(f"Ошибка парсинга времени: {e}")
        return None
    
    return None

# --- ОБРАБОТЧИКИ (HANDLERS) ---

@check_access
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    # Инициализация данных пользователя
    user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    
    keyboard = [
        [InlineKeyboardButton("📝 Создать пост", callback_data='create')],
        [InlineKeyboardButton("⏰ Отложенные", callback_data='scheduled')],
        [InlineKeyboardButton("📊 Статус", callback_data='stats')]
    ]
    
    await update.message.reply_text(
        f"👋 **Ravion Admin Panel**\nID: `{user_id}`\nБот активен и готов к работе.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@check_access
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in user_data:
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    
    data = query.data

    if data == 'create':
        context.user_data['step'] = 'game'
        await query.message.reply_text("🎮 Введите **название игры**:", parse_mode='Markdown')
    
    elif data == 'stats':
        count = len([p for p in scheduled_posts.values() if p['user_id'] == user_id])
        await query.message.reply_text(f"📊 В очереди: **{count}**\nКанал: {CHANNEL}", parse_mode='Markdown')
        
    elif data == 'preview':
        await show_preview(query.message, user_id)
        
    elif data == 'publish':
        await publish_now(query.message, user_id, context)
        
    elif data == 'schedule':
        context.user_data['step'] = 'schedule'
        await query.message.reply_text("⏰ Введите время (например: `14:30`, `завтра 10:00` или `через 1 час`):", parse_mode='Markdown')
        
    elif data == 'edit':
        kb = [
            [InlineKeyboardButton("Название", callback_data='edit_game'), InlineKeyboardButton("Описание", callback_data='edit_desc')],
            [InlineKeyboardButton("Код", callback_data='edit_code'), InlineKeyboardButton("Фото", callback_data='edit_photo')],
            [InlineKeyboardButton("Ключ вкл/выкл", callback_data='toggle_key')],
            [InlineKeyboardButton("🔙 Назад", callback_data='preview')]
        ]
        await query.message.reply_text("✏️ Что редактируем?", reply_markup=InlineKeyboardMarkup(kb))

    elif data == 'toggle_key':
        user_data[user_id]['key'] = not user_data[user_id]['key']
        await show_preview(query.message, user_id)

    elif data in ['edit_game', 'edit_desc', 'edit_code', 'edit_photo']:
        context.user_data['step'] = data.split('_')[1]
        await query.message.reply_text(f"✏️ Введите новое значение для **{data.split('_')[1]}**:", parse_mode='Markdown')

    elif data == 'scheduled':
        await show_scheduled(query.message, user_id)

    elif data.startswith('del_sch_'):
        pid = data.replace('del_sch_', '')
        if pid in scheduled_posts:
            del scheduled_posts[pid]
            await query.message.reply_text("✅ Отменено")
            await show_scheduled(query.message, user_id)
            
    elif data == 'key_yes':
        user_data[user_id]['key'] = True
        context.user_data['step'] = 'code'
        await query.message.reply_text("⚡ Вставьте **скрипт** (авто-обработка включена):", parse_mode='Markdown')
        
    elif data == 'key_no':
        user_data[user_id]['key'] = False
        context.user_data['step'] = 'code'
        await query.message.reply_text("⚡ Вставьте **скрипт** (авто-обработка включена):", parse_mode='Markdown')

async def show_preview(message, user_id):
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    kb = [
        [InlineKeyboardButton("✅ Опубликовать", callback_data='publish')],
        [InlineKeyboardButton("⏰ Запланировать", callback_data='schedule')],
        [InlineKeyboardButton("✏️ Изменить", callback_data='edit')]
    ]
    
    try:
        if d.get('photo'):
            await message.reply_photo(photo=d['photo'], caption=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        else:
            await message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка предпросмотра: {e}")
        await message.reply_text("⚠️ Ошибка предпросмотра. Возможно, текст слишком длинный или неверный формат.")

async def publish_now(message, user_id, context):
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    markup = get_channel_button()
    
    try:
        if d.get('photo'):
            await context.bot.send_photo(chat_id=CHANNEL, photo=d['photo'], caption=text, parse_mode='Markdown', reply_markup=markup)
        else:
            await context.bot.send_message(chat_id=CHANNEL, text=text, parse_mode='Markdown', reply_markup=markup)
        
        await message.reply_text("✅ Опубликовано!")
        # Очистка данных после публикации
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.reply_text(f"❌ Ошибка при отправке в канал: {e}\nУбедитесь, что бот является администратором в {CHANNEL}")

async def schedule_bg_task(app, pid):
    """Фоновая задача для проверки времени публикации"""
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            try:
                markup = get_channel_button()
                if post.get('photo'):
                    await app.bot.send_photo(chat_id=CHANNEL, photo=post['photo'], caption=post['text'], parse_mode='Markdown', reply_markup=markup)
                else:
                    await app.bot.send_message(chat_id=CHANNEL, text=post['text'], parse_mode='Markdown', reply_markup=markup)
                await app.bot.send_message(chat_id=post['user_id'], text=f"✅ Пост **{post['game']}** вышел!")
            except Exception as e:
                logger.error(f"Scheduled post error: {e}")
                await app.bot.send_message(chat_id=post['user_id'], text=f"❌ Ошибка отложки: {e}")
            
            # Удаляем из списка после попытки публикации
            if pid in scheduled_posts:
                del scheduled_posts[pid]
            break
        await asyncio.sleep(30) # Проверка каждые 30 секунд

async def show_scheduled(message, user_id):
    posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == user_id}
    if not posts:
        await message.reply_text("📭 Нет запланированных постов")
        return

    text = "📅 **Очередь публикации:**\n\n"
    kb = []
    # Сортировка по времени
    for pid, p in sorted(posts.items(), key=lambda x: x[1]['time']):
        t_str = p['time'].strftime('%d.%m %H:%M')
        game_title = p.get('game', 'Без названия')
        text += f"🔹 {t_str} - {game_title}\n"
        kb.append([InlineKeyboardButton(f"❌ Удалить {game_title}", callback_data=f'del_sch_{pid}')])
    
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

@check_access
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    step = context.user_data.get('step')
    
    if user_id not in user_data:
        # Если бот был перезагружен, создаем пустую структуру
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
        if not step:
            return # Игнорируем случайные сообщения, если нет активного шага
        
    text = update.message.text.strip() if update.message.text else ""
    
    # Обработка получения фото
    if update.message.photo:
        user_data[user_id]['photo'] = update.message.photo[-1].file_id
        await update.message.reply_text("🖼 Фото сохранено")
        if step == 'photo':
            context.user_data['step'] = None
            await show_preview(update.message, user_id)
        return

    # Команда отмены или удаления фото
    if step == 'photo' and text.lower() == 'удалить':
        user_data[user_id]['photo'] = None
        context.user_data['step'] = None
        await show_preview(update.message, user_id)
        return

    if step == 'game':
        user_data[user_id]['game'] = text
        context.user_data['step'] = 'desc'
        await update.message.reply_text("📝 Теперь введите **описание**:", parse_mode='Markdown')
        
    elif step == 'desc':
        user_data[user_id]['desc'] = text
        context.user_data['step'] = 'key'
        kb = [[InlineKeyboardButton("🔐 Да", callback_data='key_yes'), InlineKeyboardButton("🔓 Нет", callback_data='key_no')]]
        await update.message.reply_text("🔑 Нужен ключ?", reply_markup=InlineKeyboardMarkup(kb))
        
    elif step == 'code':
        # Вызываем логику обработки скрипта
        processed_code = process_script_logic(text)
        user_data[user_id]['code'] = processed_code
        context.user_data['step'] = None
        await update.message.reply_text("✅ Скрипт обработан и улучшен.")
        await show_preview(update.message, user_id)
        
    elif step == 'schedule':
        stime = parse_time(text)
        if not stime:
            await update.message.reply_text("❌ Неверный формат времени.\nПопробуйте: `15:00` или `через 2 часа`")
            return
            
        pid = f"{user_id}_{int(datetime.now().timestamp())}"
        d = user_data[user_id]
        
        scheduled_posts[pid] = {
            'user_id': user_id,
            'text': format_post(d['game'], d['desc'], d['key'], d['code']),
            'photo': d.get('photo'),
            'time': stime,
            'game': d['game']
        }
        
        # Запускаем фоновую задачу
        context.application.create_task(schedule_bg_task(context.application, pid))
        
        await update.message.reply_text(f"✅ Запланировано на **{stime.strftime('%d.%m %H:%M')}**", parse_mode='Markdown')
        context.user_data['step'] = None
        # Сброс
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}

def main():
    """Точка входа"""
    print("Запуск бота...")
    
    # Сборка приложения
    try:
        app = Application.builder().token(TOKEN).build()
        
        # Регистрация обработчиков
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button_callback))
        app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))
        
        print(f"Бот успешно запущен! Бот администрирует канал: {CHANNEL}")
        
        # Запуск polling (бесконечный цикл)
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        print(f"CRITICAL ERROR при запуске: {e}")

if __name__ == '__main__':
    main()
