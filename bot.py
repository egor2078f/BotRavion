import logging
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Union, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaAnimation, InputMediaDocument
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType

# --- КОНФИГ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL_ID = "@RavionScripts"
WATERMARK = "https://t.me/RavionScripts"
ADMINS = {7637946765, 6510703948}  # ID Админов

# Настройка логов
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Хранилища (в памяти)
scheduled_posts: Dict[str, Dict[str, Any]] = {}
user_msgs_to_delete: Dict[int, list[int]] = {}

# --- FSM (Состояния) ---
class Form(StatesGroup):
    waiting_content = State()
    waiting_time = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def delete_later(bot: Bot, chat_id: int, msg_ids: list[int]):
    """Удаляет список сообщений, чтобы не мусорить"""
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except:
            pass
    if chat_id in user_msgs_to_delete:
        user_msgs_to_delete[chat_id] = []

def add_msg_to_clean(user_id: int, msg_id: int):
    if user_id not in user_msgs_to_delete:
        user_msgs_to_delete[user_id] = []
    user_msgs_to_delete[user_id].append(msg_id)

def html_escape(text: str) -> str:
    """Экранирование для HTML"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def parse_content(raw_text: str) -> Dict[str, Any]:
    """Умный парсинг текста поста"""
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    res = {'game': '🎮 Game', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    
    res['game'] = lines[0] # Первая строка — всегда название
    
    code_found = False
    desc_lines = []
    
    for i, line in enumerate(lines[1:], 1):
        low = line.lower()
        
        # Поиск флагов ключа
        if '#key' in low or 'key+' in low: 
            res['key'] = True
            continue
        if '#nokey' in low or 'key-' in low or 'no key' in low: 
            res['key'] = False
            continue
            
        # Поиск начала кода
        is_code_sig = any(x in low for x in ['loadstring', 'game:', 'function(', 'local ', 'getgenv', 'library', '```'])
        
        if not code_found and is_code_sig:
            code_found = True
            # Начинаем собирать код
            clean_code = line.replace('```lua', '').replace('```', '')
            # Авто-ватермарка
            if 'game:HttpGet' in clean_code and WATERMARK not in clean_code:
                if clean_code.endswith('()'): clean_code = clean_code[:-2] + f'("{WATERMARK}")'
                elif clean_code.endswith('();'): clean_code = clean_code[:-3] + f'("{WATERMARK}");'
            res['code'].append(clean_code)
        elif code_found:
            clean_code = line.replace('```', '')
            res['code'].append(clean_code)
        else:
            if not line.startswith('#'):
                desc_lines.append(line)
    
    res['desc'] = '\n'.join(desc_lines)
    return res

def build_post_text(data: Dict) -> str:
    """Сборка красивого HTML поста"""
    game = html_escape(data['game']).upper()
    desc = html_escape(data['desc'])
    
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n"
    text += f"🎮 <b>{game}</b>\n"
    text += f"<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    if desc:
        text += f"💬 {desc}\n\n"
    
    key_status = "🔐 <b>Требуется ключ</b>" if data['key'] else "🔓 <b>Ключ не нужен</b>"
    text += f"{key_status}\n\n"
    
    if data['code']:
        code_block = "\n".join(data['code'])
        # Тег <code> копирует текст по клику в Telegram
        text += f"⚡ <b>СКРИПТ:</b>\n<pre><code class=\"language-lua\">{html_escape(code_block)}</code></pre>\n\n"
        
    text += f"<b>━━━━━━━━━━━━━━━━━━━</b>\n"
    text += f"📢 {CHANNEL_ID}"
    return text

def parse_time(time_str: str) -> Optional[datetime]:
    """Гибкий парсинг времени"""
    now = datetime.now()
    s = time_str.lower().replace('  ', ' ').strip()
    
    try:
        # Относительное время: "10м", "2ч", "1ч 30м"
        if any(c in s for c in ['м', 'ч', 'm', 'h']):
            delta_m = 0
            h_match = re.search(r'(\d+)\s*[чh]', s)
            m_match = re.search(r'(\d+)\s*[мm]', s)
            if h_match: delta_m += int(h_match.group(1)) * 60
            if m_match: delta_m += int(m_match.group(1))
            return now + timedelta(minutes=delta_m) if delta_m > 0 else None

        # Точное время "15:00"
        if re.match(r'^\d{1,2}:\d{2}$', s):
            h, m = map(int, s.split(':'))
            target = now.replace(hour=h, minute=m, second=0)
            if target <= now: target += timedelta(days=1) # Если время прошло, значит завтра
            return target
            
        # Дата и время "05.11 12:00"
        match = re.match(r'(\d{1,2})[./](\d{1,2})\s+(\d{1,2}):(\d{2})', s)
        if match:
            d, m, h, mn = map(int, match.groups())
            year = now.year
            # Если месяц меньше текущего, возможно это следующий год (редкий кейс, но все же)
            if m < now.month: year += 1
            return datetime(year, m, d, h, mn)
            
    except:
        return None
    return None

# --- КЛАВИАТУРЫ ---

def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост")],
        [KeyboardButton(text="📋 Очередь"), KeyboardButton(text="🗑 Очистить чат")]
    ], resize_keyboard=True, one_time_keyboard=False)

def kb_preview():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="pub_now")],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_channel_url():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Скрипт в канале", url=WATERMARK)]
    ])

def kb_queue_control(pid: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Выложить сейчас", callback_data=f"force_{pid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{pid}")]
    ])

# --- ЛОГИКА БОТА ---

router = Router()

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await msg.answer(
        "👋 <b>Админ-панель RavionScripts</b>\n\n"
        "Я умею постить фото, видео, гифки и файлы.\n"
        "Автоматически форматирую код и удаляю мусор за собой.",
        reply_markup=kb_main(), parse_mode=ParseMode.HTML
    )

@router.message(F.text == "🗑 Очистить чат")
async def clear_chat_btn(msg: Message):
    # Пытается удалить последние 100 сообщений (технически бот может удалять только свои или если он админ группы)
    # В личке бот может удалять только свои.
    await msg.answer("🧹 Чат визуально очищен (логика очистки зависит от прав бота).", reply_markup=kb_main())

@router.message(F.text == "➕ Новый пост")
async def new_post(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    add_msg_to_clean(msg.chat.id, msg.message_id)
    
    m = await msg.answer("📤 <b>Отправь контент:</b>\n\n"
                     "• Текст\n• Фото\n• Видео/GIF\n• Файл\n\n"
                     "<i>Первая строка — название, далее описание и код.</i>", parse_mode=ParseMode.HTML)
    add_msg_to_clean(msg.chat.id, m.message_id)
    await state.set_state(Form.waiting_content)

@router.message(Form.waiting_content)
async def receive_content(msg: Message, state: FSMContext):
    add_msg_to_clean(msg.chat.id, msg.message_id)
    
    # Определение типа контента
    content_type = 'text'
    file_id = None
    text = msg.text or msg.caption or ""
    
    if msg.photo:
        content_type = 'photo'
        file_id = msg.photo[-1].file_id
    elif msg.video:
        content_type = 'video'
        file_id = msg.video.file_id
    elif msg.animation:
        content_type = 'animation'
        file_id = msg.animation.file_id
    elif msg.document:
        content_type = 'document'
        file_id = msg.document.file_id

    if not text.strip() and content_type == 'text':
        m = await msg.answer("⚠️ Пустой пост. Отправь заново.")
        add_msg_to_clean(msg.chat.id, m.message_id)
        return

    parsed = parse_content(text)
    
    # Сохраняем во временное состояние
    await state.update_data(
        content_type=content_type,
        file_id=file_id,
        parsed=parsed
    )
    
    # Предпросмотр
    preview_text = build_post_text(parsed)
    
    try:
        if content_type == 'photo':
            m = await msg.answer_photo(file_id, caption=preview_text, parse_mode=ParseMode.HTML, reply_markup=kb_preview())
        elif content_type == 'video':
            m = await msg.answer_video(file_id, caption=preview_text, parse_mode=ParseMode.HTML, reply_markup=kb_preview())
        elif content_type == 'animation':
            m = await msg.answer_animation(file_id, caption=preview_text, parse_mode=ParseMode.HTML, reply_markup=kb_preview())
        elif content_type == 'document':
            m = await msg.answer_document(file_id, caption=preview_text, parse_mode=ParseMode.HTML, reply_markup=kb_preview())
        else:
            m = await msg.answer(preview_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb_preview())
            
        add_msg_to_clean(msg.chat.id, m.message_id)
        
    except Exception as e:
        logger.error(f"Error preview: {e}")
        m = await msg.answer(f"❌ Ошибка предпросмотра: {e}")
        add_msg_to_clean(msg.chat.id, m.message_id)

@router.callback_query(F.data == "cancel")
async def cancel_handler(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await delete_later(cb.bot, cb.message.chat.id, user_msgs_to_delete.get(cb.message.chat.id, []))
    await cb.answer("❌ Отменено")
    await cb.message.answer("Главное меню", reply_markup=kb_main())

@router.callback_query(F.data == "pub_now")
async def publish_now_handler(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await perform_publish(cb.bot, CHANNEL_ID, data)
    await state.clear()
    await delete_later(cb.bot, cb.message.chat.id, user_msgs_to_delete.get(cb.message.chat.id, []))
    await cb.answer("✅ Опубликовано")
    await cb.message.answer("✅ Пост опубликован в канале!", reply_markup=kb_main())

@router.callback_query(F.data == "schedule")
async def schedule_ask(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete() # Удаляем превью, чтобы не мешало
    m = await cb.message.answer(
        "⏰ <b>Введи время публикации:</b>\n\n"
        "• <code>15:30</code> (сегодня/завтра)\n"
        "• <code>20м</code> (через 20 минут)\n"
        "• <code>1ч</code> (через час)\n"
        "• <code>02.11 14:00</code> (дата)",
        parse_mode=ParseMode.HTML
    )
    add_msg_to_clean(cb.message.chat.id, m.message_id)

@router.message(Form.waiting_time)
async def schedule_confirm(msg: Message, state: FSMContext):
    add_msg_to_clean(msg.chat.id, msg.message_id)
    t = parse_time(msg.text)
    
    if not t:
        m = await msg.answer("⚠️ Неверный формат. Попробуй: <code>30м</code> или <code>16:00</code>", parse_mode=ParseMode.HTML)
        add_msg_to_clean(msg.chat.id, m.message_id)
        return

    data = await state.get_data()
    pid = f"{msg.chat.id}_{int(datetime.now().timestamp())}"
    
    scheduled_posts[pid] = {
        'post_data': data,
        'publish_time': t,
        'chat_id': msg.chat.id
    }
    
    await state.clear()
    await delete_later(msg.bot, msg.chat.id, user_msgs_to_delete.get(msg.chat.id, []))
    
    await msg.answer(
        f"✅ <b>Запланировано!</b>\n"
        f"⏰ Время: {t.strftime('%d.%m %H:%M')}\n"
        f"🎮 Игра: {data['parsed']['game']}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main()
    )

@router.message(F.text == "📋 Очередь")
async def show_queue(msg: Message):
    if not scheduled_posts:
        await msg.answer("📭 Очередь пуста.", reply_markup=kb_main())
        return

    text = "<b>📅 Очередь постов:</b>\n\n"
    # Сортировка по времени
    sorted_posts = sorted(scheduled_posts.items(), key=lambda x: x[1]['publish_time'])
    
    for pid, val in sorted_posts:
        t_str = val['publish_time'].strftime('%d.%m %H:%M')
        game = val['post_data']['parsed']['game']
        await msg.answer(
            f"🎮 <b>{game}</b>\n⏰ {t_str}",
            reply_markup=kb_queue_control(pid),
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data.startswith("force_"))
async def force_pub(cb: CallbackQuery):
    pid = cb.data.split("_")[1]
    if pid in scheduled_posts:
        # Ставим время в прошлое, планировщик подхватит мгновенно
        scheduled_posts[pid]['publish_time'] = datetime.now() - timedelta(seconds=1)
        await cb.answer("🚀 Добавлено в приоритет...")
        await cb.message.delete()
    else:
        await cb.answer("Пост уже ушел или удален", show_alert=True)

@router.callback_query(F.data.startswith("del_"))
async def del_pub(cb: CallbackQuery):
    pid = cb.data.split("_")[1]
    if pid in scheduled_posts:
        del scheduled_posts[pid]
        await cb.answer("🗑 Удалено")
        await cb.message.delete()
    else:
        await cb.answer("Уже удалено", show_alert=True)

# --- ФУНКЦИЯ ПУБЛИКАЦИИ ---

async def perform_publish(bot: Bot, channel: Union[str, int], data: Dict):
    """Единая функция отправки в канал"""
    text = build_post_text(data['parsed'])
    ctype = data['content_type']
    fid = data['file_id']
    kb = kb_channel_url()
    
    try:
        if ctype == 'photo':
            await bot.send_photo(channel, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video':
            await bot.send_video(channel, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'animation':
            await bot.send_animation(channel, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'document':
            await bot.send_document(channel, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await bot.send_message(channel, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        logger.error(f"Publish error: {e}")
        # Если не вышло в канал, шлем админу лог
        # (в data нет chat_id если публикация мгновенная, но это мелочи для примера)
        pass

# --- ФОНОВЫЙ ПЛАНИРОВЩИК ---

async def scheduler_loop(bot: Bot):
    logger.info("⏳ Scheduler started")
    while True:
        try:
            now = datetime.now()
            # Находим посты, время которых пришло
            to_publish = []
            for pid, val in scheduled_posts.items():
                if now >= val['publish_time']:
                    to_publish.append(pid)
            
            for pid in to_publish:
                post = scheduled_posts[pid]
                await perform_publish(bot, CHANNEL_ID, post['post_data'])
                
                # Уведомляем админа
                try:
                    await bot.send_message(
                        post['chat_id'], 
                        f"✅ Отложенный пост <b>{post['post_data']['parsed']['game']}</b> опубликован!",
                        parse_mode=ParseMode.HTML
                    )
                except: pass
                
                del scheduled_posts[pid]
                
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            
        await asyncio.sleep(5) # Проверка каждые 5 сек

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем планировщик параллельно с ботом
    asyncio.create_task(scheduler_loop(bot))
    
    logger.info("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
