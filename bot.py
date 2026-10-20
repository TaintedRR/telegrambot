"""
Telegram-бот для приема заявок на разработку сайтов (aiogram 3.x).

Что умеет:
  /start                  -> приветствие + inline-кнопки
  "Посмотреть портфолио"  -> ссылка на портфолио
  "Заказать сайт"         -> вопросы про нишу и бюджет, заявка уходит админу в личку
  "Задать вопрос"         -> вопрос клиента уходит админу в личку
  /cancel                 -> отмена текущего диалога

Настройки (токен, ваш ID, ссылка на портфолио) берутся из файла .env рядом с bot.py:
  BOT_TOKEN=токен_от_BotFather
  ADMIN_ID=ваш_числовой_Telegram_ID
  PORTFOLIO_URL=https://ваш-сайт.ru/portfolio
"""

import asyncio
import html
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "your bot's token").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "your id").strip()
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "https://example.com/portfolio").strip()

if not BOT_TOKEN:
    sys.exit("Ошибка: не задан BOT_TOKEN. Добавьте его в файл .env (см. инструкцию).")
if not ADMIN_ID_RAW.isdigit():
    sys.exit("Ошибка: ADMIN_ID должен быть числом. Добавьте его в файл .env (см. инструкцию).")

ADMIN_ID = int(ADMIN_ID_RAW)

# Ограничение длины ответа клиента, чтобы не упереться в лимит Telegram (4096 символов)
MAX_ANSWER_LEN = 1000

router = Router()


# --------------------------------------------------------------------------- #
# Состояния диалога (FSM)
# --------------------------------------------------------------------------- #
class OrderForm(StatesGroup):
    niche = State()
    budget = State()


class QuestionForm(StatesGroup):
    text = State()


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def main_menu() -> InlineKeyboardMarkup:
    """Inline-клавиатура главного меню."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Заказать сайт", callback_data="order")
    kb.button(text="🖼 Посмотреть портфолио", callback_data="portfolio")
    kb.button(text="❓ Задать вопрос", callback_data="question")
    kb.adjust(1)  # по одной кнопке в ряд
    return kb.as_markup()


def clip(text: str) -> str:
    """Обрезает текст и экранирует HTML, чтобы ответ клиента не ломал разметку."""
    return html.escape(text.strip()[:MAX_ANSWER_LEN])


def user_info(message: Message) -> str:
    """Строка с данными клиента для сообщения админу."""
    user = message.from_user
    if user is None:
        return "неизвестный пользователь"
    name = html.escape(user.full_name)
    username = f"@{user.username}" if user.username else "username не указан"
    return (
        f'<a href="tg://user?id={user.id}">{name}</a> '
        f"({html.escape(username)}), ID: <code>{user.id}</code>"
    )


async def notify_admin(bot: Bot, text: str) -> bool:
    """Отправляет сообщение админу в личку. Возвращает True, если доставлено."""
    try:
        await bot.send_message(ADMIN_ID, text)
        return True
    except TelegramAPIError:
        logging.exception("Не удалось отправить сообщение админу (ADMIN_ID=%s)", ADMIN_ID)
        return False


async def reply_to_callback(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Отвечает в тот же чат, где была нажата inline-кнопка."""
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=reply_markup)
    else:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = html.escape(message.from_user.first_name) if message.from_user else "друг"
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я помогу заказать разработку сайта. "
        "Выберите, что вас интересует:",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer("Сейчас нечего отменять. Выберите действие:", reply_markup=main_menu())
    else:
        await message.answer("Хорошо, отменил. Вернулся в меню:", reply_markup=main_menu())


# --------------------------------------------------------------------------- #
# Кнопки главного меню
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "portfolio")
async def show_portfolio(callback: CallbackQuery) -> None:
    await callback.answer()  # убирает "часики" на кнопке
    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть портфолио", url=PORTFOLIO_URL)
    await reply_to_callback(
        callback,
        f"Мои работы можно посмотреть здесь:\n{html.escape(PORTFOLIO_URL)}",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "order")
async def order_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(OrderForm.niche)
    await reply_to_callback(
        callback,
        "Отлично, давайте оформим заявку.\n\n"
        "<b>Какая у вас ниша?</b> (чем занимается ваш бизнес)\n\n"
        "Отмена — /cancel",
    )


@router.callback_query(F.data == "question")
async def question_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(QuestionForm.text)
    await reply_to_callback(
        callback,
        "Напишите ваш вопрос одним сообщением — я передам его разработчику.\n\n"
        "Отмена — /cancel",
    )


# --------------------------------------------------------------------------- #
# Анкета "Заказать сайт"
# --------------------------------------------------------------------------- #
@router.message(OrderForm.niche, F.text)
async def order_niche(message: Message, state: FSMContext) -> None:
    await state.update_data(niche=clip(message.text))
    await state.set_state(OrderForm.budget)
    await message.answer("<b>Какой примерный бюджет?</b>")


@router.message(OrderForm.budget, F.text)
async def order_budget(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    lead = (
        "📩 <b>Новая заявка на сайт</b>\n\n"
        f"👤 Клиент: {user_info(message)}\n"
        f"🏷 Ниша: {data.get('niche', '—')}\n"
        f"💰 Бюджет: {clip(message.text)}"
    )

    if await notify_admin(bot, lead):
        await message.answer(
            "Спасибо! Заявка отправлена, скоро с вами свяжутся. ✅",
            reply_markup=main_menu(),
        )
    else:
        await message.answer(
            "Заявку принял, но при отправке возникла техническая ошибка. "
            "Пожалуйста, попробуйте ещё раз чуть позже.",
            reply_markup=main_menu(),
        )


# --------------------------------------------------------------------------- #
# "Задать вопрос"
# --------------------------------------------------------------------------- #
@router.message(QuestionForm.text, F.text)
async def question_text(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()

    question = (
        "❓ <b>Новый вопрос от клиента</b>\n\n"
        f"👤 Клиент: {user_info(message)}\n\n"
        f"{clip(message.text)}"
    )

    if await notify_admin(bot, question):
        await message.answer("Спасибо! Вопрос передан, скоро вам ответят. ✅", reply_markup=main_menu())
    else:
        await message.answer(
            "Не получилось передать вопрос из-за технической ошибки. Попробуйте ещё раз позже.",
            reply_markup=main_menu(),
        )


# --------------------------------------------------------------------------- #
# Запасные обработчики (регистрируются последними)
# --------------------------------------------------------------------------- #
@router.message(StateFilter(OrderForm.niche, OrderForm.budget, QuestionForm.text))
async def expect_text(message: Message) -> None:
    """Клиент прислал не текст (фото, стикер и т.п.) посреди анкеты."""
    await message.answer("Пожалуйста, ответьте текстом. Для отмены — /cancel")


@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    """Любое другое сообщение вне анкеты."""
    await message.answer("Выберите действие в меню или нажмите /start:", reply_markup=main_menu())


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # На случай, если у бота ранее был установлен webhook
    await bot.delete_webhook(drop_pending_updates=False)

    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
