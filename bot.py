import asyncio
import io
import base64

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.enums import ContentType

# ================== НАСТРОЙКИ ==================

# ⚠️ В проде лучше хранить в переменных окружения
TELEGRAM_BOT_TOKEN = "8559514946:AAFwULZem2V-0Rab7DIhAyHAjK8MhyFSsvM"
APIFREE_API_KEY = "sk-pjGGTiMzewfM4iiRCkfEwbXWPQ166"

APIFREE_BASE_URL = "https://api.apifree.ai"  # из доки Seedream[web:98]

# Модели
SEEDREAM_MODEL_NAME = "bytedance/seedream-4.5"      # генерация с нуля[web:98]
GPT_IMAGE_EDIT_MODEL = "openai/gpt-image-1/edit"    # редактирование изображения[page:1]

# Для GPT-Image-1-Edit используем OpenAI‑совместимый images endpoint
APIFREE_IMAGE_URL = "https://api.apifree.ai/v1/images/generations"  # общий image API[web:59]

# ================== ИНИЦИАЛИЗАЦИЯ БОТА ==================

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Состояния пользователей:
# "img"                — ждём промт для генерации с нуля
# "lora_wait_image"    — ждём PNG для редактирования
# "lora_image_uploaded"— PNG есть, ждём промт
user_state: dict[int, str | None] = {}
# запомним PNG для lora: user_id -> file_id
user_lora_image: dict[int, str] = {}


# ================== КЛАВИАТУРА ==================

def main_keyboard() -> InlineKeyboardMarkup:
    """
    Inline-клавиатура с двумя кнопками: хочу img / хочу lora.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="хочу img", callback_data="want_img"),
                InlineKeyboardButton(text="хочу lora", callback_data="want_lora"),
            ]
        ]
    )


# ================== /start ==================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """
    Стартовое сообщение: показываем кнопки.
    """
    await message.answer(
        "Привет! Выбери, что хочешь сделать:",
        reply_markup=main_keyboard(),
    )


# ================== КНОПКА: ХОЧУ IMG ==================

@dp.callback_query(F.data == "want_img")
async def on_want_img(callback: CallbackQuery):
    """
    Режим генерации с нуля через Seedream 4.5.
    """
    user_id = callback.from_user.id
    user_state[user_id] = "img"
    await callback.message.answer("Напиши промт для нового изображения:")
    await callback.answer()


# ================== КНОПКА: ХОЧУ LORA ==================

@dp.callback_query(F.data == "want_lora")
async def on_want_lora(callback: CallbackQuery):
    """
    Режим редактирования изображения (GPT-Image-1-Edit):
    1) сначала юзер присылает PNG как документ,
    2) затем пишет промт, что нужно изменить.
    """
    user_id = callback.from_user.id
    user_state[user_id] = "lora_wait_image"
    user_lora_image.pop(user_id, None)

    await callback.message.answer(
        "Отправь PNG‑файл (как документ, не как фото), который нужно изменить."
    )
    await callback.answer()


# ================== ОБРАБОТКА ТЕКСТА ==================

@dp.message(F.text)
async def handle_text(message: Message):
    """
    Обработка текстовых сообщений:
    - если режим 'img': это промт для Seedream 4.5
    - если 'lora_image_uploaded': это промт для редактирования GPT-Image-1-Edit
    - иначе просим выбрать режим.
    """
    user_id = message.from_user.id
    mode = user_state.get(user_id)
    text = message.text.strip()

    # ---------- РЕЖИМ ГЕНЕРАЦИИ С НУЛЯ (IMG) ----------
    if mode == "img":
        if not text:
            await message.answer("Промт пустой. Напиши, что нужно сгенерировать.")
            return

        await message.answer("Генерирую изображение (Seedream 4.5), подожди немного...")

        try:
            image_bytes = await generate_image_with_seedream(text)
        except Exception as e:
            await message.answer(f"Ошибка при генерации изображения: {e}")
            return

        if not image_bytes:
            await message.answer("Не удалось получить изображение от API.")
        else:
            file = FSInputFile(io.BytesIO(image_bytes), filename="seedream.png")
            await message.answer_photo(photo=file, caption="Вот твоё изображение ✅")

        user_state[user_id] = None
        return

    # ---------- РЕЖИМ РЕДАКТИРОВАНИЯ (LORA: PNG УЖЕ ЗАГРУЖЕН) ----------
    if mode == "lora_image_uploaded":
        if not text:
            await message.answer("Промт пустой. Опиши, как нужно изменить изображение.")
            return

        file_id = user_lora_image.get(user_id)
        if not file_id:
            await message.answer(
                "Изображение не найдено. Отправь PNG ещё раз и затем напиши промт.",
                reply_markup=main_keyboard(),
            )
            user_state[user_id] = None
            return

        await message.answer("Редактирую изображение (GPT‑Image‑1‑Edit), подожди немного...")

        # 1) скачиваем PNG с серверов Telegram
        try:
            image_bytes = await download_file_bytes(file_id)
        except Exception as e:
            await message.answer(f"Не удалось скачать изображение из Telegram: {e}")
            return

        if not image_bytes:
            await message.answer("Не удалось получить байты изображения.")
            return

        # 2) шлём в GPT-Image-1-Edit
        try:
            edited_bytes = await edit_image_with_gpt_image_1(image_bytes, text)
        except Exception as e:
            await message.answer(f"Ошибка при редактировании изображения: {e}")
            return

        if not edited_bytes:
            await message.answer("Не удалось получить отредактированное изображение от API.")
        else:
            file = FSInputFile(io.BytesIO(edited_bytes), filename="edited.png")
            await message.answer_photo(photo=file, caption="Вот отредактированное изображение ✅")

        user_state[user_id] = None
        user_lora_image.pop(user_id, None)
        return

    # ---------- РЕЖИМ НЕ ВЫБРАН ----------
    await message.answer(
        "Сначала выбери режим через кнопки:",
        reply_markup=main_keyboard(),
    )


# ================== ПРИЁМ PNG ДЛЯ LORA ==================

@dp.message(F.content_type == ContentType.DOCUMENT)
async def handle_document(message: Message):
    """
    Принимаем PNG-документы в режиме lora.
    """
    user_id = message.from_user.id
    mode = user_state.get(user_id)

    if mode not in ("lora_wait_image", "lora_image_uploaded"):
        await message.answer(
            "Чтобы загрузить файл для lora-редактирования, сначала нажми кнопку \"хочу lora\".",
            reply_markup=main_keyboard(),
        )
        return

    doc = message.document
    if not doc.file_name.lower().endswith(".png"):
        await message.answer("Нужен файл в формате PNG. Отправь .png документом.")
        return

    # сохраняем file_id и просим промт
    user_lora_image[user_id] = doc.file_id
    user_state[user_id] = "lora_image_uploaded"

    await message.answer("PNG получен. Теперь напиши промт, как нужно изменить изображение.")


@dp.message(F.content_type == ContentType.PHOTO)
async def handle_photo(message: Message):
    """
    Если юзер прислал фото вместо документа.
    """
    user_id = message.from_user.id
    mode = user_state.get(user_id)

    if mode in ("lora_wait_image", "lora_image_uploaded"):
        await message.answer(
            "Пожалуйста, пришли PNG именно как *документ*, а не как фото."
        )
    else:
        await message.answer(
            "Чтобы загрузить файл для lora-редактирования, сначала нажми кнопку \"хочу lora\".",
            reply_markup=main_keyboard(),
        )


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

async def download_file_bytes(file_id: str) -> bytes | None:
    """
    Скачиваем файл из Telegram по file_id и возвращаем байты.
    """
    file = await bot.get_file(file_id)
    return await bot.download_file(file.file_path)


async def generate_image_with_seedream(prompt: str) -> bytes | None:
    """
    Seedream 4.5 через Apifree: асинхронный двухшаговый API.[web:98]

    1) POST /v1/image/submit -> request_id
    2) GET  /v1/image/{request_id}/result -> status + image_list[0]
    3) Скачиваем картинку по URL.
    """
    headers = {
        "Authorization": f"Bearer {APIFREE_API_KEY}",
        "Content-Type": "application/json",
    }

    submit_payload = {
        "model": SEEDREAM_MODEL_NAME,
        "prompt": prompt,
        "seed": 8899,     # опционально
        "size": "2K",     # или "4K"[web:98]
    }

    async with aiohttp.ClientSession() as session:
        # 1. Submit
        submit_url = f"{APIFREE_BASE_URL}/v1/image/submit"
        async with session.post(submit_url, headers=headers, json=submit_payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Submission failed HTTP {resp.status}: {text}")

            data = await resp.json()

        if data.get("code") != 200:
            raise RuntimeError(f"API Error: {data.get('code_msg') or data.get('error')}")

        request_id = data["resp_data"]["request_id"]

        # 2. Poll for result
        result_url = f"{APIFREE_BASE_URL}/v1/image/{request_id}/result"

        while True:
            await asyncio.sleep(2)  # как в примере из доки[web:98]

            async with session.get(result_url, headers=headers) as check_resp:
                check_data = await check_resp.json()

            if check_data.get("code") != 200:
                raise RuntimeError(f"Check failed: {check_data.get('code_msg')}")

            status = check_data["resp_data"]["status"]

            if status == "success":
                image_list = check_data["resp_data"].get("image_list") or []
                if not image_list:
                    return None

                img_url = image_list[0]

                # 3. Скачиваем изображение по URL
                async with session.get(img_url) as img_resp:
                    if img_resp.status != 200:
                        raise RuntimeError(
                            f"Image download failed HTTP {img_resp.status}: {await img_resp.text()}"
                        )
                    return await img_resp.read()

            if status in ("error", "failed"):
                raise RuntimeError(
                    f"Task failed: {check_data['resp_data'].get('error')}"
                )

            # status: queuing / processing — продолжаем ждать


async def edit_image_with_gpt_image_1(image_bytes: bytes, prompt: str) -> bytes | None:
    """
    Редактирование изображения через GPT-Image-1-Edit на Apifree.[page:1][web:59]

    Формат (OpenAI‑совместимый images API):
    {
      "model": "openai/gpt-image-1/edit",
      "prompt": "...",
      "image": "<base64 PNG>",
      "size": "1024x1024",
      "quality": "high",
      "response_format": "b64_json"
    }
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": GPT_IMAGE_EDIT_MODEL,
        "prompt": prompt,
        "image": b64_image,
        "size": "1024x1024",
        "quality": "high",
        "response_format": "b64_json",
    }

    headers = {
        "Authorization": f"Bearer {APIFREE_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(APIFREE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GPT-Image-1-Edit HTTP {resp.status}: {text}")

            data = await resp.json()

    if "data" not in data or not data["data"]:
        return None

    b64_img = data["data"][0].get("b64_json")
    if not b64_img:
        return None

    return base64.b64decode(b64_img)


# ================== ЗАПУСК ==================

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
