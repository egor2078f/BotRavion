import logging
import re
import asyncio
import io
import random
from datetime import datetime, timedelta
from typing import Dict, Any

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.types import (
        Message, 
        CallbackQuery, 
        InlineKeyboardButton, 
        InlineKeyboardMarkup,
        ReplyKeyboardMarkup,
        KeyboardButton,
        BufferedInputFile
    )
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("CRITICAL ERROR: Библиотеки не установлены.")
    print("Установите: pip install aiogram pillow")
    exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL = "@RavionScripts"
WATERMARK_TEXT = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ADMIN_ID = 7637946765
MODERATOR_ID = 6510703948
ALLOWED_USERS = {ADMIN_ID, MODERATOR_ID}

user_data: Dict[int, Dict[str, Any]] = {}
scheduled_posts: Dict[str, Dict[str, Any]] = {}

class PostStates(StatesGroup):
    waiting_content = State()
    waiting_time = State()

def add_watermarks(image_bytes: bytes) -> bytes:
    """Добавляет множество полупрозрачных водяных знаков на изображение"""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
        image = image.convert('RGBA')
        
        watermark_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(watermark_layer)
        
        # Размер шрифта - 8% от меньшей стороны
        min_dimension = min(width, height)
        font_size = int(min_dimension * 0.08)
        
        # Загружаем шрифт
        try:
            font = ImageFont.truetype("/system/fonts/Roboto-Bold.ttf", font_size)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", font_size)
                except:
                    font = ImageFont.load_default()
        
        # Получаем размер текста
        bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Случайное количество водяных знаков от 8 до 13
        num_watermarks = random.randint(8, 13)
        
        # Прозрачность 80% = 255 * 0.2 = 51 (20% непрозрачности)
        opacity = 51
        
        # Генерируем случайные позиции
        positions = []
        for _ in range(num_watermarks):
            # Случайные координаты с учётом размера текста
            x = random.randint(-text_width // 2, width - text_width // 2)
            y = random.randint(-text_height // 2, height - text_height // 2)
            
            # Случайный угол наклона от -45 до 45 градусов
            angle = random.randint(-45, 45)
            
            positions.append((x, y, angle))
        
        # Рисуем водяные знаки
        for x, y, angle in positions:
            # Создаём временный слой для поворота
            temp_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_layer)
            
            # Рисуем текст
            temp_draw.text(
                (x, y),
                WATERMARK_TEXT,
                font=font,
                fill=(255, 255, 255, opacity)
            )
            
            # Поворачиваем
            if angle != 0:
                rotated = temp_layer.rotate(angle, expand=False, resample=Image.BICUBIC)
                watermark_layer = Image.alpha_composite(watermark_layer, rotated)
            else:
                watermark_layer = Image.alpha_composite(watermark_layer, temp_layer)
        
        # Объединяем слои
        result = Image.alpha_composite(image, watermark_layer)
        
        # Сохраняем в bytes
        output = io.BytesIO()
        result.convert('RGB').save(output, format='PNG', quality=95)
        output.seek(0)
        
        return output.read()
        
    except Exception as e:
        logger.error(f"Ошибка добавления водяных знаков: {e}")
        return image_bytes

def process_script_logic(text: str) -> list:
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

def parse_content(text: str) -> Dict[str, Any]:
    lines = text.strip().split('\n')
    
    result = {
        'game': '',
        'desc': '',
        'key': False,
        'code': []
    }
    
    if not lines:
        return result
    
    result['game'] = lines[0].strip()
    
    code_start_idx = None
    
    for i, line in enumerate(lines[1:], start=1):
        lower_line = line.lower().strip()
        
        if '#key' in lower_line or 'key+' in lower_line or '+key' in lower_line:
            result['key'] = True
            continue
        elif '#nokey' in lower_line or 'key-' in lower_line or '-key' in lower_line or 'no key' in lower_line:
            result['key'] = False
            continue
        
        if code_start_idx is None and any(k in lower_line for k in ['loadstring', 'game:', 'local ', 'function', '```']):
            code_start_idx = i
            break
    
    if code_start_idx and code_start_idx > 1:
        desc_lines = []
        for i in range(1, code_start_idx):
            line = lines[i].strip()
            if line and not line.startswith('#') and 'key' not in line.lower():
                desc_lines.append(line)
        result['desc'] = ' '.join(desc_lines)
    
    if code_start_idx:
        code_text = '\n'.join(lines[code_start_idx:])
        result['code'] = process_script_logic(code_text)
    
    return result

def format_post(game_name: str, description: str, has_key: bool, code: list) -> str:
    lines = []
    
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🎮  {game_name.upper()}")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    if description:
        lines.append(f"💬  {description}")
        lines.append("")
    
    key_emoji = "🔐" if has_key else "🔓"
    key_text = "Требуется ключ" if has_key else "Ключ не нужен"
    lines.append(f"{key_emoji}  {key_text}")
    lines.append("")
    
    if code:
        lines.append("⚡  СКРИПТ:")
        lines.append("```lua")
        lines.extend(code)
        lines.append("```")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📢  {CHANNEL}")
    
    return '\n'.join(lines)

def get_channel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Перейти в канал", url='https://t.me/RavionScripts')
    ]])

def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Новый пост")],
        [KeyboardButton(text="Мои посты"), KeyboardButton(text="Статистика")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать", callback_data='publish')],
        [InlineKeyboardButton(text="Отложить", callback_data='schedule')],
        [InlineKeyboardButton(text="Изменить", callback_data='edit')],
        [InlineKeyboardButton(text="Отменить", callback_data='cancel')]
    ])

def parse_time(time_str: str) -> datetime | None:
    try:
        now = datetime.now()
        time_str = time_str.lower().strip()
        
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target + timedelta(days=1) if target <= now else target
        
        if 'ч' in time_str or 'м' in time_str:
            hours = 0
            minutes = 0
            
            hours_match = re.search(r'(\d+)ч', time_str)
            if hours_match:
                hours = int(hours_match.group(1))
            
            mins_match = re.search(r'(\d+)м', time_str)
            if mins_match:
                minutes = int(mins_match.group(1))
            
            if hours or minutes:
                return now + timedelta(hours=hours, minutes=minutes)
        
        if 'завтра' in time_str:
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_match:
                return (now + timedelta(days=1)).replace(
                    hour=int(time_match.group(1)), 
                    minute=int(time_match.group(2)), 
                    second=0, 
                    microsecond=0
                )
    except Exception as e:
        logger.error(f"Ошибка парсинга времени: {e}")
        return None
    
    return None

def check_access(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not check_access(user_id):
        return
    
    user_data[user_id] = {
        'game': '', 
        'desc': '', 
        'key': False, 
        'code': [], 
        'photo': None
    }
    
    await state.clear()
    
    username = message.from_user.first_name or "Администратор"
    
    await message.answer(
        f"Привет, {username}!\n\n"
        f"Я помогу создавать посты для канала {CHANNEL}\n\n"
        f"Формат сообщения:\n"
        f"Название игры\n"
        f"Описание (необязательно)\n"
        f"#key или #nokey\n"
        f"loadstring(game:HttpGet(...))())\n\n"
        f"Можно прикрепить фото (автоматически добавлю водяные знаки)\n\n"
        f"Нажми 'Новый пост' чтобы начать",
        reply_markup=get_main_keyboard()
    )

@router.message(F.text == "Новый пост")
async def new_post(message: Message, state: FSMContext):
    if not check_access(message.from_user.id):
        return
    
    await state.set_state(PostStates.waiting_content)
    await message.answer(
        "Отправь контент поста\n\n"
        "Пример:\n"
        "Blox Fruits\n"
        "Лучший скрипт для фарма\n"
        "#key\n"
        "loadstring(game:HttpGet('ссылка'))()\n\n"
        "Можешь прикрепить фото\n"
        "/cancel для отмены"
    )

@router.message(F.text == "Мои посты")
async def my_posts(message: Message):
    if not check_access(message.from_user.id):
        return
    
    await show_scheduled(message, message.from_user.id)

@router.message(F.text == "Статистика")
async def stats(message: Message):
    if not check_access(message.from_user.id):
        return
    
    user_id = message.from_user.id
    count = len([p for p in scheduled_posts.values() if p['user_id'] == user_id])
    
    await message.answer(
        f"Статистика:\n\n"
        f"Постов в очереди: {count}\n"
        f"Канал: {CHANNEL}\n"
        f"Статус: Активен ✅"
    )

@router.message(Command("cancel"))
async def cancel_action(message: Message, state: FSMContext):
    if not check_access(message.from_user.id):
        return
    
    await state.clear()
    await message.answer("Действие отменено", reply_markup=get_main_keyboard())

@router.message(PostStates.waiting_content)
async def process_content(message: Message, state: FSMContext, bot: Bot):
    if not check_access(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    photo_id = None
    photo_bytes = None
    text_content = ""
    
    # Обработка фото с водяными знаками
    if message.photo:
        status = await message.answer("Добавляю водяные знаки на фото...")
        
        file = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file.file_path)
        
        # Добавляем водяные знаки
        watermarked_bytes = add_watermarks(photo_bytes.read())
        photo_id = watermarked_bytes
        
        await status.delete()
        text_content = message.caption or ""
        
    elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
        status = await message.answer("Добавляю водяные знаки на изображение...")
        
        file = await bot.get_file(message.document.file_id)
        photo_bytes = await bot.download_file(file.file_path)
        
        # Добавляем водяные знаки
        watermarked_bytes = add_watermarks(photo_bytes.read())
        photo_id = watermarked_bytes
        
        await status.delete()
        text_content = message.caption or ""
    else:
        text_content = message.text or ""
    
    if not text_content.strip():
        await message.answer("Пожалуйста, отправьте текст поста")
        return
    
    parsed = parse_content(text_content)
    
    if not parsed['game']:
        await message.answer("Не удалось определить название игры. Первая строка должна быть названием")
        return
    
    user_data[user_id] = {
        'game': parsed['game'],
        'desc': parsed['desc'],
        'key': parsed['key'],
        'code': parsed['code'],
        'photo': photo_id
    }
    
    await state.clear()
    
    await show_preview(message, user_id)

@router.message(PostStates.waiting_time)
async def process_schedule_time(message: Message, state: FSMContext):
    if not check_access(message.from_user.id):
        return
    
    stime = parse_time(message.text)
    if not stime:
        await message.answer(
            "Неверный формат времени\n\n"
            "Примеры:\n"
            "14:30 - сегодня в 14:30\n"
            "2ч - через 2 часа\n"
            "30м - через 30 минут\n"
            "завтра 10:00 - завтра в 10:00"
        )
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
    
    asyncio.create_task(schedule_bg_task(message.bot, pid))
    
    await message.answer(
        f"Пост запланирован\n\n"
        f"Игра: {d['game']}\n"
        f"Время: {stime.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Посмотреть очередь: Мои посты",
        reply_markup=get_main_keyboard()
    )
    
    await state.clear()
    user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}

@router.callback_query(F.data == 'publish')
async def callback_publish(callback: CallbackQuery, state: FSMContext):
    if not check_access(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await publish_now(callback.message, callback.from_user.id, callback.bot)
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == 'schedule')
async def callback_schedule(callback: CallbackQuery, state: FSMContext):
    if not check_access(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.waiting_time)
    await callback.message.answer(
        "Когда опубликовать?\n\n"
        "Примеры:\n"
        "14:30 - сегодня в 14:30\n"
        "2ч - через 2 часа\n"
        "30м - через 30 минут\n"
        "завтра 10:00 - завтра в 10:00"
    )
    await callback.answer()

@router.callback_query(F.data == 'edit')
async def callback_edit(callback: CallbackQuery, state: FSMContext):
    if not check_access(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.waiting_content)
    await callback.message.answer("Отправь новый контент в том же формате")
    await callback.answer()

@router.callback_query(F.data == 'cancel')
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    if not check_access(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    user_id = callback.from_user.id
    user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    
    await state.clear()
    await callback.message.answer("Пост отменён", reply_markup=get_main_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith('del_'))
async def callback_delete_scheduled(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    pid = callback.data.replace('del_', '')
    if pid in scheduled_posts:
        game_name = scheduled_posts[pid].get('game', 'Пост')
        del scheduled_posts[pid]
        await callback.answer(f"{game_name} удалён", show_alert=True)
        await show_scheduled(callback.message, callback.from_user.id)
    await callback.answer()

async def show_preview(message: Message, user_id: int):
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    
    try:
        if d.get('photo'):
            if isinstance(d['photo'], bytes):
                photo_file = BufferedInputFile(d['photo'], filename='watermarked.png')
                await message.answer_photo(
                    photo=photo_file, 
                    caption="Предпросмотр:\n\n" + text, 
                    parse_mode='Markdown', 
                    reply_markup=get_action_keyboard()
                )
            else:
                await message.answer_photo(
                    photo=d['photo'], 
                    caption="Предпросмотр:\n\n" + text, 
                    parse_mode='Markdown', 
                    reply_markup=get_action_keyboard()
                )
        else:
            await message.answer(
                "Предпросмотр:\n\n" + text, 
                parse_mode='Markdown', 
                reply_markup=get_action_keyboard()
            )
    except Exception as e:
        logger.error(f"Ошибка предпросмотра: {e}")
        await message.answer("Ошибка отображения. Попробуйте заново создать пост")

async def publish_now(message: Message, user_id: int, bot: Bot):
    d = user_data[user_id]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    markup = get_channel_button()
    
    try:
        if d.get('photo'):
            if isinstance(d['photo'], bytes):
                photo_file = BufferedInputFile(d['photo'], filename='watermarked.png')
                await bot.send_photo(
                    chat_id=CHANNEL, 
                    photo=photo_file, 
                    caption=text, 
                    parse_mode='Markdown', 
                    reply_markup=markup
                )
            else:
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
        
        await message.answer(
            f"Успешно опубликовано\n\n"
            f"Игра: {d['game']}\n"
            f"Канал: {CHANNEL}",
            reply_markup=get_main_keyboard()
        )
        
        user_data[user_id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.answer(f"Ошибка публикации: {str(e)[:200]}")

async def schedule_bg_task(bot: Bot, pid: str):
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            try:
                markup = get_channel_button()
                if post.get('photo'):
                    if isinstance(post['photo'], bytes):
                        photo_file = BufferedInputFile(post['photo'], filename='watermarked.png')
                        await bot.send_photo(
                            chat_id=CHANNEL, 
                            photo=photo_file, 
                            caption=post['text'], 
                            parse_mode='Markdown', 
                            reply_markup=markup
                        )
                    else:
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
                    text=f"Пост опубликован\n\nИгра: {post['game']}\nКанал: {CHANNEL}"
                )
            except Exception as e:
                logger.error(f"Ошибка отложенной публикации: {e}")
                await bot.send_message(
                    chat_id=post['user_id'], 
                    text=f"Ошибка публикации: {str(e)[:200]}"
                )
            
            if pid in scheduled_posts:
                del scheduled_posts[pid]
            break
        await asyncio.sleep(30)

async def show_scheduled(message: Message, user_id: int):
    posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == user_id}
    
    if not posts:
        await message.answer("Нет запланированных постов\n\nСоздайте новый пост и отложите его")
        return

    text = "Запланированные посты:\n\n"
    kb = []
    
    for pid, p in sorted(posts.items(), key=lambda x: x[1]['time']):
        time_left = p['time'] - datetime.now()
        hours_left = int(time_left.total_seconds() / 3600)
        minutes_left = int((time_left.total_seconds() % 3600) / 60)
        
        time_str = p['time'].strftime('%d.%m в %H:%M')
        game_title = p.get('game', 'Без названия')
        
        if hours_left > 0:
            countdown = f"через {hours_left}ч {minutes_left}м"
        else:
            countdown = f"через {minutes_left}м"
        
        text += f"{game_title}\n{time_str} ({countdown})\n\n"
        kb.append([InlineKeyboardButton(
            text=f"❌ {game_title}", 
            callback_data=f'del_{pid}'
        )])
    
    await message.answer(
        text, 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def main():
    logger.info("Запуск бота...")
    
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    dp.include_router(router)
    
    logger.info(f"Бот запущен! Канал: {CHANNEL}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"CRITICAL ERROR: {e}")
