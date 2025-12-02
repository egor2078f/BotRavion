import logging
import asyncio
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- КОНФИГУРАЦИЯ ---
# Используем тот же токен, что и в основном боте.
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
router = Router()

@router.message(CommandStart())
async def maintenance_start(msg: Message):
    """Отвечает всем пользователям, что бот на обслуживании."""
    
    # Сообщение о починке
    message = (
        "🛠️ <b>ВАЖНОЕ ОБЪЯВЛЕНИЕ</b> 🛠️\n\n"
        "Бот временно находится на <b>техническом обслуживании (починке)</b>.\n\n"
        "Мы уже работаем над улучшениями и скоро вернемся в строй!\n"
        "Пожалуйста, попробуйте зайти немного позже. Спасибо за понимание."
    )
    
    try:
        await msg.answer(
            message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение о починке пользователю {msg.from_user.id}: {e}")

async def main():
    # Настройки по умолчанию
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Удаляем вебхуки и запускаем долгий опрос
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("--- БОТ В РЕЖИМЕ ПОЧИНКИ ---")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
