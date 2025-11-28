import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.types import (
        Message, 
        CallbackQuery, 
        InlineKeyboardButton, 
        InlineKeyboardMarkup,
        PhotoSize
    )
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
except ImportError:
    print("CRITICAL ERROR: Библиотека 'aiogram' не установлена.")
    print("Установите: pip install -r requirements.txt")
    exit(1)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ADMIN_ID = 7637946765
MODERATOR_ID = 6510703948
ALLOWED_USERS = {ADMIN_ID, MODERATOR_ID}

# Хранилища данных
user_data: Dict[int, Dict[str, Any]] = {}
scheduled_posts: Dict[str, Dict[str, Any]] = {}

# --- FSM STATES ---
class PostStates(StatesGroup):
    game = State()
    desc = State()
    key = State()
    code = State()
    schedule = State()
    edit_game = State()
    edit_desc = State()
    edit_code = State()
    edit_photo = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def process_script_logic(text: str) -> list:
    """Обработка скрипта с добавлением ватермарки"""
    code_lines = []
    in_code_block = False
    
    raw_lines = text.split('\n')
    
    for line in raw_lines:
        stripped = line.strip()
        
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
            
        is_code_line = (
            in_code_block or 
            any(k in line.lower() for k in ['loadstring', 'game:', 'local', 'function', 'http', 'script', 'args'])
        )

        if is_code_line:
            if 'loadstring' in stripped and 'game:HttpGet' in stripped:
                if stripped.endswith('()'):
                    stripped = stripped[:-2] + f'("{WATERMARK_URL}")'
                elif stripped.endswith('();'):
                    stripped = stripped[:-3] + f'("{WATERMARK_URL}");'
            
            code_lines.append(stripped)
            
    return code_lines

def format_post(game_name: str, description: str, has_key: bool, code: list) -> str:
    """Формирование текста поста"""
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

def get_channel_button() -> InlineKeyboardMarkup:
    """Кнопка канала"""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Open Channel", url='https://t.me/RavionScripts')
    ]])

def parse_time(time_str: str) -> datetime | None:
    """Парсинг времени для отложенной публикации"""
    try:
        now = datetime.now()
        time_str = time_str.lower().strip()
        
        # Формат 14:30
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target + timedelta(days=1) if target <= now else target
        
        # Формат: завтра 14:30
        if 'завтра' in time_str:
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_match:
                return (now + timedelta(days=1)).replace(
                    hour=int(time_match.group(1)), 
                    minute=int(time_match.group(2)), 
                    second=0, 
                    microsecond=0
                )
                
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

def check_access(user_id: int) -> bool:
    """Проверка доступа"""
    return user_id in ALLOWED_USERS

# --- РОУТЕР ---
router = Router()

# --- ОБРАБОТЧИКИ ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    user_id = message.from_user.id
    
    if not check_access(user_id):
        return
    
    # Инициализация данных пользователя
    user_data[user_id] = {
        'game': '', 
        'desc': '', 
        'key': False, 
        'code': [], 
        'photo': None
    }
    
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать пост", callback_data='create')],
        [InlineKeyboardButton(text="⏰ Отложенные", callback_data='scheduled')],
        [InlineKeyboardButton(text="📊 Статус", callback_data='stats')]
    ])
    
    await message.answer(
        f"👋 **Ravion Admin Panel**\nID: `{user_id}`\nБот активен и готов к работе.",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

@router.callback_query(F.data == 'create')
async def callback_create(callback: CallbackQuery, state: FSMContext):
    """Создание нового поста"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.game)
    await callback.message.answer("🎮 Введите **название игры**:", parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data == 'stats')
async def callback_stats(callback: CallbackQuery):
    """Статистика"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = callback.from_user.id
    count = len([p for p in scheduled_posts.values() if p['user_id'] == user_id])
    await callback.message.answer(
        f"📊 В очереди: **{count}**\nКанал: {CHANNEL}", 
        parse_mode='Markdown'
    )
    await callback.answer()

@router.callback_query(F.data == 'preview')
async def callback_preview(callback: CallbackQuery):
    """Предпросмотр поста"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await show_preview(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == 'publish')
async def callback_publish(callback: CallbackQuery):
    """Публикация поста"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await publish_now(callback.message, callback.from_user.id, callback.bot)
    await callback.answer()

@router.callback_query(F.data == 'schedule')
async def callback_schedule(callback: CallbackQuery, state: FSMContext):
    """Запланировать пост"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.schedule)
    await callback.message.answer(
        "⏰ Введите время (например: `14:30`, `завтра 10:00` или `через 1 час`):", 
        parse_mode='Markdown'
    )
    await callback.answer()

@router.callback_query(F.data == 'edit')
async def callback_edit(callback: CallbackQuery):
    """Редактирование поста"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Название", callback_data='edit_game'), 
            InlineKeyboardButton(text="Описание", callback_data='edit_desc')
        ],
        [
            InlineKeyboardButton(text="Код", callback_data='edit_code'), 
            InlineKeyboardButton(text="Фото", callback_data='edit_photo')
        ],
        [InlineKeyboardButton(text="Ключ вкл/выкл", callback_data='toggle_key')],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='preview')]
    ])
    await callback.message.answer("✏️ Что редактируем?", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == 'toggle_key')
async def callback_toggle_key(callback: CallbackQuery):
    """Переключение ключа"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = callback.from_user.id
    user_data[user_id]['key'] = not user_data[user_id]['key']
    await show_preview(callback.message, user_id)
    await callback.answer()

@router.callback_query(F.data.startswith('edit_'))
async def callback_edit_field(callback: CallbackQuery, state: FSMContext):
    """Редактирование поля"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    field = callback.data.replace('edit_', '')
    state_map = {
        'game': PostStates.edit_game,
        'desc': PostStates.edit_desc,
        'code': PostStates.edit_code,
        'photo': PostStates.edit_photo
    }
    
    await state.set_state(state_map[field])
    await callback.message.answer(f"✏️ Введите новое значение для **{field}**:", parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data == 'scheduled')
async def callback_scheduled(callback: CallbackQuery):
    """Показать отложенные посты"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await show_scheduled(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.startswith('del_sch_'))
async def callback_delete_scheduled(callback: CallbackQuery):
    """Удалить отложенный пост"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    pid = callback.data.replace('del_sch_', '')
    if pid in scheduled_posts:
        del scheduled_posts[pid]
        await callback.message.answer("✅ Отменено")
        await show_scheduled(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.in_(['key_yes', 'key_no']))
async def callback_key(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора ключа"""
    if not check_access(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = callback.from_user.id
    user_data[user_id]['key'] = (callback.data == 'key_yes')
    
    await state.set_state(PostStates.code)
    await callback.message.answer("⚡ Вставьте **скрипт** (авто-обработка включена):", parse_mode='Markdown')
    await callback.answer()

# --- ОБРАБОТЧИКИ СОСТОЯНИЙ ---
@router.message(PostStates.game)
async def process_game(message: Message, state: FSMContext):
    """Обработка названия игры"""
    if not check_access(message.from_user.id):
        return
    
    user_data[message.from_user.id]['game'] = message.text.strip()
    await state.set_state(PostStates.desc)
    await message.answer("📝 Теперь введите **описание**:", parse_mode='Markdown')

@router.message(PostStates.desc)
async def process_desc(message: Message, state: FSMContext):
    """Обработка описания"""
    if not check_access(message.from_user.id):
        return
    
    user_data[message.from_user.id]['desc'] = message.text.strip()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔐 Да", callback_data='key_yes'), 
        InlineKeyboardButton(text="🔓 Нет", callback_data='key_no')
    ]])
    await message.answer("🔑 Нужен ключ?", reply_markup=kb)

@router.message(PostStates.code)
async def process_code(message: Message, state: FSMContext):
    """Обработка кода"""
    if not check_access(message.from_user.id):
        return
    
    processed_code = process_script_logic(message.text)
    user_data[message.from_user.id]['code'] = processed_code
    
    await state.clear()
    await message.answer("✅ Скрипт обработан и улучшен.")
    await show_preview(message, message.from_user.id)

@router.message(PostStates.schedule)
async def process_schedule(message: Message, state: FSMContext):
    """Обработка времени отложки"""
    if not check_access(message.from_user.id):
        return
    
    stime = parse_time(message.text)
    if not stime:
        await message.answer("❌ Неверный формат времени.\nПопробуйте: `15:00` или `через 2 часа`", parse_mode='Markdown')
        return
    
    user_id = message.from_user.id
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
    asyncio.create_task(schedule_bg_task(message.bot, pid))
    
    await message.answer(f"✅ Запланировано на **{stime.strftime('%d.%m %H:%M')}**", parse_mode='Markdown')
    await state.clear()
    
    # Сброс данных
    user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}

@router.message(PostStates.edit_game)
async def process_edit_game(message: Message, state: FSMContext):
    """Редактирование названия"""
    if not check_access(message.from_user.id):
        return
    
    user_data[message.from_user.id]['game'] = message.text.strip()
    await state.clear()
    await show_preview(message, message.from_user.id)

@router.message(PostStates.edit_desc)
async def process_edit_desc(message: Message, state: FSMContext):
    """Редактирование описания"""
    if not check_access(message.from_user.id):
        return
    
    user_data[message.from_user.id]['desc'] = message.text.strip()
    await state.clear()
    await show_preview(message, message.from_user.id)

@router.message(PostStates.edit_code)
async def process_edit_code(message: Message, state: FSMContext):
    """Редактирование кода"""
    if not check_access(message.from_user.id):
        return
    
    processed_code = process_script_logic(message.text)
    user_data[message.from_user.id]['code'] = processed_code
    await state.clear()
    await show_preview(message, message.from_user.id)

@router.message(PostStates.edit_photo, F.photo)
async def process_edit_photo(message: Message, state: FSMContext):
    """Редактирование фото"""
    if not check_access(message.from_user.id):
        return
    
    user_data[message.from_user.id]['photo'] = message.photo[-1].file_id
    await state.clear()
    await message.answer("🖼 Фото обновлено")
    await show_preview(message, message.from_user.id)

@router.message(PostStates.edit_photo, F.text)
async def process_delete_photo(message: Message, state: FSMContext):
    """Удаление фото"""
    if not check_access(message.from_user.id):
        return
    
    if message.text.lower().strip() == 'удалить':
        user_data[message.from_user.id]['photo'] = None
        await state.clear()
        await message.answer("🖼 Фото удалено")
        await show_preview(message, message.from_user.id)

@router.message(F.photo)
async def handle_photo(message: Message):
    """Обработка фото вне состояний"""
    if not check_access(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id in user_data:
        user_data[user_id]['photo'] = message.photo[-1].file_id
        await message.answer("🖼 Фото сохранено")

# --- ВСПОМОГАТЕЛЬНЫЕ ASYNC ФУНКЦИИ ---
async def show_preview(message: Message, user_id: int):
    """Показать предпросмотр поста"""
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data='publish')],
        [InlineKeyboardButton(text="⏰ Запланировать", callback_data='schedule')],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data='edit')]
    ])
    
    try:
        if d.get('photo'):
            await message.answer_photo(
                photo=d['photo'], 
                caption=text, 
                parse_mode='Markdown', 
                reply_markup=kb
            )
        else:
            await message.answer(text, parse_mode='Markdown', reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка предпросмотра: {e}")
        await message.answer("⚠️ Ошибка предпросмотра. Возможно, текст слишком длинный или неверный формат.")

async def publish_now(message: Message, user_id: int, bot: Bot):
    """Опубликовать пост сейчас"""
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    markup = get_channel_button()
    
    try:
        if d.get('photo'):
            await bot.send_photo(
                chat_id=CHANNEL, 
                photo=d['photo'], 
                caption=text, 
                parse_mode='Markdown', 
                reply_markup=markup
            )
        else:
            await bot.send_message(
                chat_id=CHANNEL, 
                text=text, 
                parse_mode='Markdown', 
                reply_markup=markup
            )
        
        await message.answer("✅ Опубликовано!")
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.answer(f"❌ Ошибка при отправке в канал: {e}\nУбедитесь, что бот является администратором в {CHANNEL}")

async def schedule_bg_task(bot: Bot, pid: str):
    """Фоновая задача для отложенной публикации"""
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            try:
                markup = get_channel_button()
                if post.get('photo'):
                    await bot.send_photo(
                        chat_id=CHANNEL, 
                        photo=post['photo'], 
                        caption=post['text'], 
                        parse_mode='Markdown', 
                        reply_markup=markup
                    )
                else:
                    await bot.send_message(
                        chat_id=CHANNEL, 
                        text=post['text'], 
                        parse_mode='Markdown', 
                        reply_markup=markup
                    )
                await bot.send_message(
                    chat_id=post['user_id'], 
                    text=f"✅ Пост **{post['game']}** опубликован!", 
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка отложенной публикации: {e}")
                await bot.send_message(
                    chat_id=post['user_id'], 
                    text=f"❌ Ошибка отложки: {e}"
                )
            
            if pid in scheduled_posts:
                del scheduled_posts[pid]
            break
        await asyncio.sleep(30)

async def show_scheduled(message: Message, user_id: int):
    """Показать отложенные посты"""
    posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == user_id}
    if not posts:
        await message.answer("📭 Нет запланированных постов")
        return

    text = "📅 **Очередь публикации:**\n\n"
    kb = []
    
    for pid, p in sorted(posts.items(), key=lambda x: x[1]['time']):
        t_str = p['time'].strftime('%d.%m %H:%M')
        game_title = p.get('game', 'Без названия')
        text += f"🔹 {t_str} - {game_title}\n"
        kb.append([InlineKeyboardButton(
            text=f"❌ Удалить {game_title}", 
            callback_data=f'del_sch_{pid}'
        )])
    
    await message.answer(
        text, 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), 
        parse_mode='Markdown'
    )

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    """Точка входа"""
    logger.info("Запуск бота...")
    
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрация роутера
    dp.include_router(router)
    
    logger.info(f"Бот успешно запущен! Канал: {CHANNEL}")
    
    # Удаляем webhook и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"CRITICAL ERROR: {e}")
