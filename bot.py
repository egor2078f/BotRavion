from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import logging
import re
from datetime import datetime, timedelta
import asyncio

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота и канал
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
DEFAULT_CHANNEL = "@RavionScripts"

# Хранилище данных пользователей
user_data_storage = {}
scheduled_posts = {}

def detect_script_language(text):
    """Определяет язык скрипта"""
    text_lower = text.lower()
    
    if 'loadstring' in text_lower or 'game:httpget' in text_lower or 'game:getservice' in text_lower:
        return 'lua'
    elif 'function' in text_lower and ('end' in text_lower or 'return' in text_lower):
        return 'lua'
    elif 'import' in text_lower or 'def ' in text_lower:
        return 'python'
    elif 'console.log' in text_lower or 'const ' in text_lower:
        return 'javascript'
    
    return 'lua'

def extract_script_info(text):
    """Извлекает информацию о скрипте из текста"""
    lines = text.strip().split('\n')
    
    game_name = None
    has_key = None
    description = []
    script_code = []
    urls = []
    other_lines = []
    
    code_started = False
    
    for line in lines:
        line_stripped = line.strip()
        
        # Пропускаем пустые строки
        if not line_stripped:
            continue
        
        # Ищем код скрипта
        if ('loadstring' in line_stripped.lower() or 
            'game:httpget' in line_stripped.lower() or
            'game:getservice' in line_stripped.lower() or
            'local ' in line_stripped.lower() or
            'function' in line_stripped.lower()):
            code_started = True
            script_code.append(line_stripped)
            
            # Извлекаем URL
            url_match = re.search(r'https?://[^\s\'")\]]+', line_stripped)
            if url_match:
                urls.append(url_match.group(0))
            continue
        
        # Если уже начался код, продолжаем его собирать
        if code_started:
            script_code.append(line_stripped)
            continue
        
        # Ищем название игры (только в первых строках)
        if not game_name and not code_started:
            game_patterns = [
                r'скрипт\s+(?:на|для|к|—|–|-)\s*["\']?(.+?)["\']?$',
                r'скрипт\s+["\']?(.+?)["\']?$',
                r'^(.+?)\s+(?:скрипт|esp|hack)',
                r'игр[аы]\s*:?\s*["\']?(.+?)["\']?$',
                r'^["\']?([^:\n]+?)["\']?\s*$'
            ]
            
            for pattern in game_patterns:
                match = re.search(pattern, line_stripped, re.IGNORECASE)
                if match:
                    potential_name = match.group(1).strip(' ":\'–—-')
                    # Проверяем, что это не ключ и не техническая информация
                    if (len(potential_name) > 2 and 
                        'ключ' not in potential_name.lower() and
                        'key' not in potential_name.lower() and
                        not potential_name.startswith('http')):
                        game_name = potential_name
                        continue
        
        # Ищем информацию о ключе
        if 'ключ' in line_stripped.lower() or 'key' in line_stripped.lower():
            if '✅' in line or 'да' in line_stripped.lower() or 'yes' in line_stripped.lower() or 'требуется' in line_stripped.lower():
                has_key = True
            elif '❌' in line or 'нет' in line_stripped.lower() or 'no' in line_stripped.lower() or 'не требуется' in line_stripped.lower():
                has_key = False
            continue
        
        # Всё остальное - это описание
        if not code_started:
            # Пропускаем строку, если в ней было найдено название игры
            if game_name and game_name in line:
                continue
            other_lines.append(line_stripped)
    
    # Формируем описание из оставшихся строк
    description = [line for line in other_lines if line and len(line) > 3]
    
    return {
        'game_name': game_name,
        'has_key': has_key,
        'description': description,
        'script_code': script_code,
        'urls': urls
    }

def format_post(text, custom_description=None):
    """Автоматически форматирует пост с улучшенным дизайном"""
    info = extract_script_info(text)
    
    formatted_lines = []
    
    # Красивый заголовок с разделителем
    formatted_lines.append("━━━━━━━━━━━━━━━━━━━")
    
    # Название игры
    if info['game_name']:
        formatted_lines.append(f"🎮 **{info['game_name']}**")
    else:
        formatted_lines.append("🎮 **Новый скрипт**")
    
    formatted_lines.append("━━━━━━━━━━━━━━━━━━━\n")
    
    # Описание (пользовательское или автоматическое)
    if custom_description:
        formatted_lines.append(f"📝 {custom_description}\n")
    elif info['description']:
        formatted_lines.append(f"📝 {' '.join(info['description'])}\n")
    
    # Информация о ключе
    if info['has_key'] is not None:
        key_status = "✅ Требуется" if info['has_key'] else "❌ Не требуется"
        formatted_lines.append(f"🔑 **Ключ:** {key_status}\n")
    
    # Код скрипта с красивым оформлением
    if info['script_code']:
        language = detect_script_language(' '.join(info['script_code']))
        formatted_lines.append("⚡ **Скрипт:**")
        formatted_lines.append(f"```{language}")
        formatted_lines.extend(info['script_code'])
        formatted_lines.append("```\n")
    
    # Нижний разделитель
    formatted_lines.append("━━━━━━━━━━━━━━━━━━━")
    formatted_lines.append("💎 **Ravion Scripts** — Лучшие скрипты здесь!")
    
    formatted_text = '\n'.join(formatted_lines)
    
    # Кнопки по умолчанию
    default_buttons = [
        {'text': '📱 Наш канал', 'url': 'https://t.me/RavionScripts'},
        {'text': '💬 Чат', 'url': 'https://t.me/RavionScripts'}, # Замени на свой чат
    ]
    
    return formatted_text, default_buttons, info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    user_data_storage[user_id] = {
        'text': '',
        'photo': None,
        'channel_id': DEFAULT_CHANNEL,
        'buttons': [],
        'mode': 'auto',
        'description': '',
        'schedule_time': None
    }
    
    keyboard = [
        [InlineKeyboardButton("🤖 Быстрый пост (авто)", callback_data='mode_auto')],
        [InlineKeyboardButton("✏️ Расширенный режим", callback_data='mode_manual')],
        [InlineKeyboardButton("⏰ Отложенные посты", callback_data='view_scheduled')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🚀 **Ravion Scripts — Бот для постов**\n\n"
        "✨ **Возможности:**\n"
        "• Автоматическое оформление\n"
        "• Добавление описания\n"
        "• Отложенная публикация\n"
        "• Красивый дизайн\n\n"
        "Выбери режим работы:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id not in user_data_storage:
        user_data_storage[user_id] = {
            'text': '',
            'photo': None,
            'channel_id': DEFAULT_CHANNEL,
            'buttons': [],
            'mode': 'auto',
            'description': '',
            'schedule_time': None
        }
    
    if query.data == 'mode_auto':
        user_data_storage[user_id]['mode'] = 'auto'
        await query.message.reply_text(
            "🤖 **Быстрый режим активирован!**\n\n"
            "**Как использовать:**\n"
            "1️⃣ Отправь текст со скриптом\n"
            "2️⃣ При необходимости добавь описание\n"
            "3️⃣ Добавь фото (опционально)\n"
            "4️⃣ Готово!\n\n"
            "📝 **Пример:**\n"
            "```\n"
            "Скрипт на Murder Mystery 2\n"
            "Ключ ❌\n"
            "Описание: Крутой ESP для игры\n\n"
            "loadstring(game:HttpGet('https://...'))()```\n\n"
            "💡 Отправь свой скрипт сейчас!",
            parse_mode='Markdown'
        )
    
    elif query.data == 'mode_manual':
        user_data_storage[user_id]['mode'] = 'manual'
        await show_manual_menu(query.message)
    
    elif query.data == 'help':
        await query.message.reply_text(
            "📚 **Подробная инструкция:**\n\n"
            "**🤖 Быстрый режим:**\n"
            "Отправь текст в любом формате:\n"
            "```\n"
            "Скрипт на Blox Fruits\n"
            "Ключ ❌\n"
            "Описание: Авто-фарм фруктов\n\n"
            "loadstring(...)()```\n\n"
            "**✏️ Расширенный режим:**\n"
            "• Полный контроль над постом\n"
            "• Добавление описания отдельно\n"
            "• Настройка кнопок\n\n"
            "**⏰ Отложенная публикация:**\n"
            "• Запланируй пост на нужное время\n"
            "• Бот опубликует автоматически\n\n"
            "**🎨 Автооформление включает:**\n"
            "✅ Красивые разделители\n"
            "✅ Подсветку кода\n"
            "✅ Эмодзи и форматирование\n"
            "✅ Кнопки канала",
            parse_mode='Markdown'
        )
    
    elif query.data == 'add_text':
        context.user_data['waiting_for'] = 'text'
        await query.message.reply_text(
            "📝 Отправь текст со скриптом\n\n"
            "Можешь включить всё:\n"
            "• Название игры\n"
            "• Наличие ключа\n"
            "• Описание\n"
            "• Код скрипта"
        )
    
    elif query.data == 'add_description':
        context.user_data['waiting_for'] = 'description'
        await query.message.reply_text(
            "📝 **Добавь описание к скрипту**\n\n"
            "Например:\n"
            "• Автофарм всех фруктов\n"
            "• ESP для всех игроков\n"
            "• Бесконечные деньги\n\n"
            "Напиши описание:"
        )
    
    elif query.data == 'add_photo':
        context.user_data['waiting_for'] = 'photo'
        await query.message.reply_text("🖼 Отправь фото или ссылку на изображение")
    
    elif query.data == 'add_buttons':
        keyboard = [
            [InlineKeyboardButton("➕ Добавить кнопку", callback_data='add_single_button')],
            [InlineKeyboardButton("📋 Показать кнопки", callback_data='show_buttons')],
            [InlineKeyboardButton("🗑 Удалить кнопки", callback_data='clear_buttons')],
            [InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        buttons_count = len(user_data_storage[user_id].get('buttons', []))
        await query.message.reply_text(
            f"🔘 **Управление кнопками**\n\nКнопок: {buttons_count}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif query.data == 'add_single_button':
        context.user_data['waiting_for'] = 'button'
        await query.message.reply_text(
            "🔘 Отправь кнопку в формате:\n\n"
            "`Текст | ссылка`\n\n"
            "**Примеры:**\n"
            "`💎 Донат | https://boosty.to/...`\n"
            "`💬 Discord | https://discord.gg/...`\n"
            "`🎮 Играть | https://roblox.com/...`",
            parse_mode='Markdown'
        )
    
    elif query.data == 'show_buttons':
        buttons = user_data_storage[user_id].get('buttons', [])
        if not buttons:
            await query.message.reply_text("❌ Кнопки не добавлены")
        else:
            text = "📋 **Кнопки:**\n\n"
            for i, btn in enumerate(buttons, 1):
                text += f"{i}. {btn['text']} → `{btn['url']}`\n"
            await query.message.reply_text(text, parse_mode='Markdown')
    
    elif query.data == 'clear_buttons':
        user_data_storage[user_id]['buttons'] = []
        await query.message.reply_text("✅ Кнопки удалены!")
    
    elif query.data == 'back_to_menu':
        if user_data_storage[user_id].get('mode') == 'manual':
            await show_manual_menu(query.message)
        else:
            await show_preview_menu(query.message, user_id)
    
    elif query.data == 'preview':
        await show_preview(query.message, user_id, context)
    
    elif query.data == 'publish_now':
        await publish_post(query.message, user_id, context)
    
    elif query.data == 'schedule_post':
        context.user_data['waiting_for'] = 'schedule_time'
        await query.message.reply_text(
            "⏰ **Отложенная публикация**\n\n"
            "Отправь время в одном из форматов:\n\n"
            "**Формат 1:** `14:30` (сегодня в 14:30)\n"
            "**Формат 2:** `завтра 10:00`\n"
            "**Формат 3:** `через 2 часа`\n"
            "**Формат 4:** `через 30 минут`\n\n"
            "Напиши время:",
            parse_mode='Markdown'
        )
    
    elif query.data == 'edit_post':
        context.user_data['waiting_for'] = 'text'
        await query.message.reply_text("✏️ Отправь новый текст для поста")
    
    elif query.data == 'view_scheduled':
        await show_scheduled_posts(query.message, user_id)
    
    elif query.data.startswith('cancel_scheduled_'):
        post_id = query.data.replace('cancel_scheduled_', '')
        if post_id in scheduled_posts:
            del scheduled_posts[post_id]
            await query.message.reply_text("✅ Пост отменён!")
            await show_scheduled_posts(query.message, user_id)
        else:
            await query.message.reply_text("❌ Пост не найден")
    
    elif query.data == 'clear':
        user_data_storage[user_id] = {
            'text': '',
            'photo': None,
            'channel_id': DEFAULT_CHANNEL,
            'buttons': [],
            'mode': user_data_storage[user_id].get('mode', 'auto'),
            'description': '',
            'schedule_time': None
        }
        await query.message.reply_text("🗑 Всё очищено!")

async def show_manual_menu(message):
    """Показать расширенное меню"""
    keyboard = [
        [InlineKeyboardButton("📝 Текст скрипта", callback_data='add_text')],
        [InlineKeyboardButton("💬 Описание", callback_data='add_description')],
        [InlineKeyboardButton("🖼 Фото", callback_data='add_photo')],
        [InlineKeyboardButton("🔘 Кнопки", callback_data='add_buttons')],
        [InlineKeyboardButton("👀 Предпросмотр", callback_data='preview')],
        [InlineKeyboardButton("✅ Опубликовать", callback_data='publish_now'),
         InlineKeyboardButton("⏰ Отложить", callback_data='schedule_post')],
        [InlineKeyboardButton("🗑 Очистить", callback_data='clear')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text(
        "✏️ **Расширенный режим**\n\nВыбери действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_preview_menu(message, user_id):
    """Показать меню после предпросмотра"""
    keyboard = [
        [InlineKeyboardButton("✅ Опубликовать сейчас", callback_data='publish_now')],
        [InlineKeyboardButton("⏰ Отложить публикацию", callback_data='schedule_post')],
        [InlineKeyboardButton("✏️ Редактировать", callback_data='edit_post')],
        [InlineKeyboardButton("💬 Изменить описание", callback_data='add_description')],
        [InlineKeyboardButton("➕ Добавить кнопки", callback_data='add_buttons')],
        [InlineKeyboardButton("🗑 Очистить", callback_data='clear')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text("Что делаем дальше?", reply_markup=reply_markup)

def parse_schedule_time(time_str):
    """Парсит время для отложенной публикации"""
    now = datetime.now()
    time_str = time_str.lower().strip()
    
    # Формат: "14:30" или "14:30:00"
    time_match = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$', time_str)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Если время уже прошло, планируем на завтра
        if target_time <= now:
            target_time += timedelta(days=1)
        
        return target_time
    
    # Формат: "завтра 14:30"
    if 'завтра' in time_str:
        time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            target_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target_time
    
    # Формат: "через 2 часа"
    hours_match = re.search(r'через\s+(\d+)\s+час', time_str)
    if hours_match:
        hours = int(hours_match.group(1))
        return now + timedelta(hours=hours)
    
    # Формат: "через 30 минут"
    minutes_match = re.search(r'через\s+(\d+)\s+минут', time_str)
    if minutes_match:
        minutes = int(minutes_match.group(1))
        return now + timedelta(minutes=minutes)
    
    return None

async def show_scheduled_posts(message, user_id):
    """Показать запланированные посты"""
    user_posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == user_id}
    
    if not user_posts:
        await message.reply_text("📅 У тебя нет запланированных постов")
        return
    
    text = "📅 **Запланированные посты:**\n\n"
    keyboard = []
    
    for post_id, post_data in user_posts.items():
        time_str = post_data['schedule_time'].strftime('%d.%m.%Y %H:%M')
        text += f"⏰ {time_str}\n"
        
        # Показываем превью
        preview = post_data['text'][:50] + "..." if len(post_data['text']) > 50 else post_data['text']
        text += f"📝 {preview}\n\n"
        
        keyboard.append([InlineKeyboardButton(f"❌ Отменить ({time_str})", callback_data=f'cancel_scheduled_{post_id}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def schedule_post_task(application, post_id):
    """Задача для отложенной публикации"""
    while post_id in scheduled_posts:
        post_data = scheduled_posts[post_id]
        now = datetime.now()
        
        if now >= post_data['schedule_time']:
            # Публикуем пост
            try:
                buttons = post_data.get('buttons', [])
                reply_markup = None
                
                if buttons:
                    keyboard = []
                    for btn in buttons:
                        keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
                    reply_markup = InlineKeyboardMarkup(keyboard)
                
                if post_data.get('photo'):
                    if post_data.get('photo_type') == 'file_id':
                        await application.bot.send_photo(
                            chat_id=post_data['channel_id'],
                            photo=post_data['photo'],
                            caption=post_data['text'],
                            parse_mode='Markdown',
                            reply_markup=reply_markup
                        )
                    else:
                        full_text = f"{post_data['text']}\n\n{post_data['photo']}"
                        await application.bot.send_message(
                            chat_id=post_data['channel_id'],
                            text=full_text,
                            parse_mode='Markdown',
                            reply_markup=reply_markup
                        )
                else:
                    await application.bot.send_message(
                        chat_id=post_data['channel_id'],
                        text=post_data['text'],
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                
                # Уведомляем пользователя
                await application.bot.send_message(
                    chat_id=post_data['user_id'],
                    text="✅ Запланированный пост опубликован!"
                )
                
                logger.info(f"Опубликован запланированный пост {post_id}")
                
            except Exception as e:
                logger.error(f"Ошибка публикации запланированного поста: {e}")
                await application.bot.send_message(
                    chat_id=post_data['user_id'],
                    text=f"❌ Ошибка публикации запланированного поста: {str(e)}"
                )
            
            # Удаляем пост из очереди
            del scheduled_posts[post_id]
            break
        
        # Проверяем каждую минуту
        await asyncio.sleep(60)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений"""
    user_id = update.effective_user.id
    
    if user_id not in user_data_storage:
        user_data_storage[user_id] = {
            'text': '',
            'photo': None,
            'channel_id': DEFAULT_CHANNEL,
            'buttons': [],
            'mode': 'auto',
            'description': '',
            'schedule_time': None
        }
    
    waiting_for = context.user_data.get('waiting_for')
    
    # Обработка описания
    if waiting_for == 'description':
        user_data_storage[user_id]['description'] = update.message.text.strip()
        context.user_data['waiting_for'] = None
        
        await update.message.reply_text("✅ Описание сохранено!")
        
        # Если есть текст, обновляем пост с новым описанием
        if user_data_storage[user_id].get('text'):
            # Переформатируем пост с новым описанием
            raw_text = user_data_storage[user_id].get('raw_text', '')
            if raw_text:
                formatted_text, default_buttons, info = format_post(raw_text, user_data_storage[user_id]['description'])
                user_data_storage[user_id]['text'] = formatted_text
                
                if not user_data_storage[user_id]['buttons']:
                    user_data_storage[user_id]['buttons'] = default_buttons
        
        if user_data_storage[user_id].get('mode') == 'manual':
            await show_manual_menu(update.message)
        else:
            await show_preview(update.message, user_id, context)
        return
    
    # Обработка времени для отложенной публикации
    if waiting_for == 'schedule_time':
        schedule_time = parse_schedule_time(update.message.text)
        
        if not schedule_time:
            await update.message.reply_text(
                "❌ Неверный формат времени!\n\n"
                "Используй:\n"
                "• `14:30`\n"
                "• `завтра 10:00`\n"
                "• `через 2 часа`\n"
                "• `через 30 минут`",
                parse_mode='Markdown'
            )
            return
        
        context.user_data['waiting_for'] = None
        
        # Сохраняем запланированный пост
        post_id = f"{user_id}_{int(datetime.now().timestamp())}"
        scheduled_posts[post_id] = {
            'user_id': user_id,
            'text': user_data_storage[user_id]['text'],
            'photo': user_data_storage[user_id].get('photo'),
            'photo_type': user_data_storage[user_id].get('photo_type'),
            'buttons': user_data_storage[user_id].get('buttons', []),
            'channel_id': user_data_storage[user_id].get('channel_id', DEFAULT_CHANNEL),
            'schedule_time': schedule_time
        }
        
        # Запускаем задачу для публикации
        asyncio.create_task(schedule_post_task(context.application, post_id))
        
        time_str = schedule_time.strftime('%d.%m.%Y в %H:%M')
        await update.message.reply_text(
            f"✅ **Пост запланирован!**\n\n"
            f"⏰ Будет опубликован: {time_str}\n\n"
            f"Посмотреть все запланированные посты: /start → Отложенные посты",
            parse_mode='Markdown'
        )
        
        # Очищаем данные
        user_data_storage[user_id] = {
            'text': '', 'photo': None,
            'channel_id': DEFAULT_CHANNEL,
            'buttons': [],
            'mode': user_data_storage[user_id].get('mode', 'auto'),
            'description': '',
            'schedule_time': None
        }
        return
    
    # Обработка текста
    if waiting_for == 'text':
        user_data_storage[user_id]['raw_text'] = update.message.text
        formatted_text, default_buttons, info = format_post(
            update.message.text, 
            user_data_storage[user_id].get('description')
        )
        user_data_storage[user_id]['text'] = formatted_text
        
        if not user_data_storage[user_id]['buttons']:
            user_data_storage[user_id]['buttons'] = default_buttons
        
        context.user_data['waiting_for'] = None
        await update.message.reply_text("✅ Текст обработан и оформлен!")
        
        if user_data_storage[user_id].get('mode') == 'manual':
            await show_manual_menu(update.message)
        else:
            await show_preview(update.message, user_id, context)
        return
    
    # Обработка фото
    if waiting_for == 'photo':
        if update.message.photo:
            user_data_storage[user_id]['photo'] = update.message.photo[-1].file_id
            user_data_storage[user_id]['photo_type'] = 'file_id'
        elif update.message.text and ('http://' in update.message.text or 'https://' in update.message.text):
            user_data_storage[user_id]['photo'] = update.message.text
            user_data_storage[user_id]['photo_type'] = 'url'
        else:
            await update.message.reply_text("❌ Отправь фото или ссылку")
            return
        
        context.user_data['waiting_for'] = None
        await update.message.reply_text("✅ Фото сохранено!")
        
        if user_data_storage[user_id].get('mode') == 'manual':
            await show_manual_menu(update.message)
        else:
            await show_preview(update.message, user_id, context)
        return
    
    # Обработка кнопок
    if waiting_for == 'button':
        try:
            lines = update.message.text.strip().split('\n')
            buttons_added = 0
            
            for line in lines:
                if '|' not in line:
                    continue
                
                parts = line.split('|', 1)
                if len(parts) != 2:
                    continue
                
                btn_text = parts[0].strip()
                btn_url = parts[1].strip()
                
                if btn_text and btn_url:
                    user_data_storage[user_id]['buttons'].append({
                        'text': btn_text,
                        'url': btn_url
                    })
                    buttons_added += 1
            
            if buttons_added > 0:
                context.user_data['waiting_for'] = None
                await update.message.reply_text(f"✅ Добавлено кнопок: {buttons_added}")
                await show_manual_menu(update.message)
            else:
                await update.message.reply_text("❌ Формат: `Текст | ссылка`", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        return
    
    # Авто-режим - обработка обычных сообщений
    if user_data_storage[user_id].get('mode') == 'auto':
        if update.message.photo:
            # Сохраняем фото
            user_data_storage[user_id]['photo'] = update.message.photo[-1].file_id
            user_data_storage[user_id]['photo_type'] = 'file_id'
            
            # Если есть подпись, обрабатываем её
            if update.message.caption:
                user_data_storage[user_id]['raw_text'] = update.message.caption
                formatted_text, default_buttons, info = format_post(
                    update.message.caption,
                    user_data_storage[user_id].get('description')
                )
                user_data_storage[user_id]['text'] = formatted_text
                
                if not user_data_storage[user_id]['buttons']:
                    user_data_storage[user_id]['buttons'] = default_buttons
                
                await update.message.reply_text("✅ Фото и текст обработаны!")
                await show_preview(update.message, user_id, context)
            else:
                await update.message.reply_text(
                    "✅ Фото сохранено!\n\n"
                    "Теперь отправь текст со скриптом или добавь описание кнопкой ниже."
                )
        
        elif update.message.text:
            # Сохраняем оригинальный текст
            user_data_storage[user_id]['raw_text'] = update.message.text
            
            # Автоматически форматируем текст
            formatted_text, default_buttons, info = format_post(
                update.message.text,
                user_data_storage[user_id].get('description')
            )
            user_data_storage[user_id]['text'] = formatted_text
            
            # Добавляем кнопки по умолчанию если их нет
            if not user_data_storage[user_id]['buttons']:
                user_data_storage[user_id]['buttons'] = default_buttons
            
            await update.message.reply_text("✅ Текст обработан и красиво оформлен!")
            await show_preview(update.message, user_id, context)

async def show_preview(message, user_id, context):
    """Показать предпросмотр с улучшенным дизайном"""
    data = user_data_storage.get(user_id, {})
    
    if not data.get('text') and not data.get('photo'):
        await message.reply_text("❌ Нет данных для предпросмотра")
        return
    
    text = data.get('text', 'Нет текста')
    buttons = data.get('buttons', [])
    
    # Клавиатура с кнопками поста
    post_markup = None
    if buttons:
        keyboard = []
        for btn in buttons:
            keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
        post_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await message.reply_text(
            "━━━━━━━━━━━━━━━━━━━\n"
            "👀 **ПРЕДПРОСМОТР ПОСТА**\n"
            "━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown'
        )
        
        if data.get('photo'):
            if data.get('photo_type') == 'file_id':
                await message.reply_photo(
                    photo=data['photo'],
                    caption=text,
                    parse_mode='Markdown',
                    reply_markup=post_markup
                )
            else:
                full_text = f"{text}\n\n🖼 Фото: {data['photo']}"
                await message.reply_text(
                    full_text,
                    parse_mode='Markdown',
                    reply_markup=post_markup
                )
        else:
            await message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=post_markup
            )
        
        # Показываем меню действий
        await show_preview_menu(message, user_id)
        
    except Exception as e:
        logger.error(f"Ошибка предпросмотра: {e}")
        await message.reply_text(
            f"❌ Ошибка предпросмотра: {str(e)}\n\n"
            "Возможно проблема с форматированием.\n"
            "Попробуй отредактировать текст."
        )

async def publish_post(message, user_id, context):
    """Опубликовать пост немедленно"""
    data = user_data_storage.get(user_id, {})
    
    if not data.get('text') and not data.get('photo'):
        await message.reply_text("❌ Нет данных для публикации!")
        return
    
    try:
        text = data.get('text', '')
        channel_id = data.get('channel_id', DEFAULT_CHANNEL)
        buttons = data.get('buttons', [])
        
        # Клавиатура с кнопками
        reply_markup = None
        if buttons:
            keyboard = []
            for btn in buttons:
                keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        if data.get('photo'):
            if data.get('photo_type') == 'file_id':
                await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=data['photo'],
                    caption=text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                full_text = f"{text}\n\n{data['photo']}"
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=full_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        else:
            await context.bot.send_message(
                chat_id=channel_id,
                text=text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        await message.reply_text(
            "✅ **Пост успешно опубликован!**\n\n"
            f"📱 Канал: {channel_id}\n"
            "🎉 Проверь канал!",
            parse_mode='Markdown'
        )
        
        # Очищаем данные
        user_data_storage[user_id] = {
            'text': '',
            'photo': None,
            'channel_id': DEFAULT_CHANNEL,
            'buttons': [],
            'mode': data.get('mode', 'auto'),
            'description': '',
            'schedule_time': None
        }
        
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.reply_text(
            f"❌ **Ошибка публикации:**\n\n"
            f"`{str(e)}`\n\n"
            "**Проверь:**\n"
            "• Бот админ в канале\n"
            "• Права на публикацию\n"
            "• Форматирование текста",
            parse_mode='Markdown'
        )

def main():
    """Запуск бота"""
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))
    
    logger.info("🚀 Ravion Scripts Bot запущен!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎮 RAVION SCRIPTS BOT")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("✅ Бот успешно запущен!")
    print(f"📱 Канал: {DEFAULT_CHANNEL}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
