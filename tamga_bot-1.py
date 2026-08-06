"""
Тамга AI — кичи бизнестер үчүн кыргызча копирайтинг Telegram-боту
(Google Gemini акысыз API менен иштейт)

Орнотуу:
    pip install python-telegram-bot google-generativeai

Иштетүү үчүн 2 нерсе керек:
    1. TELEGRAM_BOT_TOKEN — @BotFather'дан алас (Telegram'да "/newbot" жаз)
    2. GEMINI_API_KEY — aistudio.google.com сайтынан акысыз алас (карта керек эмес)

Иштетүү:
    export TELEGRAM_BOT_TOKEN="сенин_токениң"
    export GEMINI_API_KEY="сенин_ачкычың"
    python tamga_bot.py
"""

import os
import logging
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# Маектин баскычтары
BUSINESS, PRODUCT, PRICE, TYPE_CHOICE, TONE_CHOICE = range(5)

TYPES = {
    "instagram": ("Instagram посту", "Кыска, эмоционалдуу, эмодзи менен"),
    "ad": ("Жарнама тексти", "Кыска жана таасирдүү, чакырык менен"),
    "description": ("Товар сүрөттөмөсү", "Толук, өзгөчөлүктөрү менен"),
    "sms": ("SMS/WhatsApp билдирүү", "Өтө кыска, түз сунуш"),
}

TONES = ["достук", "расмий", "тамашалуу", "шашылыш", "философиялык"]

# Канча вариант жаратуу керек — өзгөртсө болот
VARIANT_COUNT = 10


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Салам! Мен — Тамга AI 🏷️\n\n"
        "Сага товар/кызматың үчүн даяр жарнама тексти жазып берем.\n\n"
        "Бизнесиңдин атын жаз (мисалы: Асель Гүлдөр):"
    )
    return BUSINESS


async def business_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["business"] = update.message.text
    await update.message.reply_text("Товар же кызмат жөнүндө жаз (кыска сүрөттөп бер):")
    return PRODUCT


async def product_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["product"] = update.message.text
    await update.message.reply_text("Баасы канча? (жок болсо — жөн эле \"жок\" деп жаз)")
    return PRICE


async def price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["price"] = update.message.text
    keyboard = [
        [InlineKeyboardButton(label, callback_data=key)]
        for key, (label, _) in TYPES.items()
    ]
    await update.message.reply_text(
        "Кайсы форматта жазайын?", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TYPE_CHOICE


async def type_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    keyboard = [[InlineKeyboardButton(t, callback_data=t)] for t in TONES]
    await query.edit_message_text(
        "Кайсы обондо болсун?", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TONE_CHOICE


async def tone_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["tone"] = query.data
    await query.edit_message_text("Жазып жатам… ⏳")

    ud = context.user_data
    type_label, type_hint = TYPES[ud["type"]]

    prompt = f"""Сен кичи бизнестер үчүн кыргызча копирайтинг адисисиң, сөз чеберчилигиң менен таанымал устасың. Төмөнкү маалымат боюнча "{type_label}" ({type_hint}) жаз.

Бизнес: {ud['business']}
Товар/кызмат: {ud['product']}
Баасы: {ud['price']}
Обон: {ud['tone']}

Талаптар:
- Толугу менен кыргыз тилинде жаз
- {VARIANT_COUNT} ар башка вариант бер, ар бирин "---" менен бөл
- Ар бир вариант бири-биринен айырмаланган стилде/сөздөрдө болсун (кайталанбасын)
- Сөздөрдү жөнөкөй эмес, көркөм, таамай жана эстеп каларлык кыл — окуган/уккан адам таң калсын, көңүлүнө тие турган сөз тандап жаз
- Кыргыз тилинин байлыгын колдон — метафора, теңеме, элдик накыл сөздөрдүн духун (түз цитата эмес, өз стилиңде) колдонсоң болот
- Эгер обон "философиялык" болсо — тереңирээк ой жүгүрт, жашоо, маани, баалуулук жөнүндө кыска ой кошуп жаз, бирок дагы деле болсо бизнести/товарды даңазалоо максатын унутпа
- Ар бир вариант кыска, натыйжалуу жана "{ud['tone']}" обонунда болсун
- Эч кандай түшүндүрмө жазба — түз эле варианттарды бер"""

    try:
        response = model.generate_content(prompt)
        text = response.text
        variants = [v.strip() for v in text.split("---") if v.strip()]

        header = f"✅ *{ud['business']}* үчүн даяр варианттар ({len(variants)} даана):\n\n"
        chunks = []
        current = header
        for i, v in enumerate(variants, 1):
            piece = f"*Вариант {i}:*\n{v}\n\n"
            # Telegram чеги ~4096, коопсуздук үчүн 3500дөн ашырбайбыз
            if len(current) + len(piece) > 3500:
                chunks.append(current)
                current = piece
            else:
                current += piece
        chunks.append(current)

        for chunk in chunks:
            await query.message.reply_text(chunk, parse_mode="Markdown")

        await query.message.reply_text("Дагы бир текст керек болсо — /start жаз")
    except Exception as e:
        logging.error(f"API ката: {e}")
        await query.message.reply_text("Ката кетти, кайра аракет кыл: /start")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Токтотулду. Кайра баштоо үчүн /start жаз")
    return ConversationHandler.END


def main():
    if not TELEGRAM_TOKEN or not GEMINI_KEY:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN жана GEMINI_API_KEY environment "
            "өзгөрмөлөрүн орното керек (жогорудагы комментарийди кара)"
        )

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, business_step)],
            PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_step)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_step)],
            TYPE_CHOICE: [CallbackQueryHandler(type_step)],
            TONE_CHOICE: [CallbackQueryHandler(tone_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    print("Бот иштеп баштады...")
    app.run_polling()


if __name__ == "__main__":
    main()
