"""
@Keyword_FinderBot — a free Telegram keyword research bot.

Data sources (all free, no API key needed beyond your bot token):
  - Google Autocomplete   -> keyword suggestions, "alphabet soup", question keywords
  - Google Trends (pytrends) -> interest over time, rising/top related queries
"""

import asyncio
import logging
import os
import string
from collections import OrderedDict

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("keyword_finder_bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it in Railway's Variables tab (or a local .env when testing)."
    )

TELEGRAM_MSG_LIMIT = 4096
AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
REQUEST_TIMEOUT = 6

QUESTION_WORDS = [
    "what", "why", "how", "when", "where", "who", "which",
    "will", "can", "are", "is", "does",
]
PREPOSITIONS = [
    "for", "with", "without", "to", "near", "vs", "like", "on",
]


def _fetch_suggestions(query: str) -> list[str]:
    """Hit Google's autocomplete endpoint for a single query string."""
    try:
        resp = requests.get(
            AUTOCOMPLETE_URL,
            params={"client": "firefox", "q": query},
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data[1] if len(data) > 1 else []
    except Exception as exc:
        logger.warning("autocomplete fetch failed for %r: %s", query, exc)
        return []


def get_alphabet_soup(seed: str) -> "OrderedDict[str, None]":
    results: "OrderedDict[str, None]" = OrderedDict()
    results.update((s, None) for s in _fetch_suggestions(seed))
    for ch in string.ascii_lowercase + string.digits:
        for s in _fetch_suggestions(f"{seed} {ch}"):
            results.setdefault(s, None)
    return results


def get_question_keywords(seed: str) -> "OrderedDict[str, None]":
    results: "OrderedDict[str, None]" = OrderedDict()
    for w in QUESTION_WORDS:
        for s in _fetch_suggestions(f"{w} {seed}"):
            results.setdefault(s, None)
    return results


def get_preposition_keywords(seed: str) -> "OrderedDict[str, None]":
    results: "OrderedDict[str, None]" = OrderedDict()
    for p in PREPOSITIONS:
        for s in _fetch_suggestions(f"{seed} {p}"):
            results.setdefault(s, None)
    return results


def get_trends_summary(seed: str) -> dict:
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=360)
    pytrends.build_payload([seed], timeframe="today 12-m")

    out = {"direction": "unknown", "avg_interest": None, "top": [], "rising": []}

    iot = pytrends.interest_over_time()
    if not iot.empty and seed in iot.columns:
        series = iot[seed]
        out["avg_interest"] = round(float(series.mean()), 1)
        first_half_avg = series.iloc[: len(series) // 2].mean()
        second_half_avg = series.iloc[len(series) // 2 :].mean()
        if second_half_avg > first_half_avg * 1.1:
            out["direction"] = "rising 📈"
        elif second_half_avg < first_half_avg * 0.9:
            out["direction"] = "falling 📉"
        else:
            out["direction"] = "steady ➡️"

    related = pytrends.related_queries()
    seed_related = related.get(seed) or {}
    top_df = seed_related.get("top")
    rising_df = seed_related.get("rising")
    if top_df is not None and not top_df.empty:
        out["top"] = top_df["query"].head(10).tolist()
    if rising_df is not None and not rising_df.empty:
        out["rising"] = rising_df["query"].head(10).tolist()

    return out


def format_list(title: str, items: list[str], limit: int = 40) -> str:
    if not items:
        return f"*{title}*\n_No results found._"
    shown = items[:limit]
    body = "\n".join(f"• {i}" for i in shown)
    extra = f"\n_(+{len(items) - limit} more)_" if len(items) > limit else ""
    return f"*{title}* ({len(items)} found)\n{body}{extra}"


async def send_long_message(update: Update, text: str) -> None:
    if len(text) <= TELEGRAM_MSG_LIMIT:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > TELEGRAM_MSG_LIMIT:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


def get_seed_arg(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    return " ".join(context.args).strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Keyword Finder Bot!*\n\n"
        "I generate free keyword research data from Google Autocomplete "
        "and Google Trends — no paid API needed.\n\n"
        "*Commands:*\n"
        "/keywords <seed> — A-Z + 0-9 autocomplete suggestions\n"
        "/questions <seed> — question-based keywords\n"
        "/prepositions <seed> — preposition-based keywords\n"
        "/trends <seed> — 12-month trend direction\n"
        "/related <seed> — top & rising related searches\n"
        "/full <seed> — everything above in one report\n\n"
        "Example: `/full coffee shop`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/keywords <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(f"🔎 Gathering keyword suggestions for *{seed}*…", parse_mode=ParseMode.MARKDOWN)
    results = await asyncio.to_thread(get_alphabet_soup, seed)
    await send_long_message(update, format_list(f"Keyword suggestions for “{seed}”", list(results.keys())))


async def questions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/questions <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(f"❓ Gathering question keywords for *{seed}*…", parse_mode=ParseMode.MARKDOWN)
    results = await asyncio.to_thread(get_question_keywords, seed)
    await send_long_message(update, format_list(f"Question keywords for “{seed}”", list(results.keys())))


async def prepositions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/prepositions <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(f"🔗 Gathering preposition keywords for *{seed}*…", parse_mode=ParseMode.MARKDOWN)
    results = await asyncio.to_thread(get_preposition_keywords, seed)
    await send_long_message(update, format_list(f"Preposition keywords for “{seed}”", list(results.keys())))


async def trends_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/trends <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(f"📊 Checking Google Trends for *{seed}*…", parse_mode=ParseMode.MARKDOWN)
    try:
        data = await asyncio.to_thread(get_trends_summary, seed)
    except Exception as exc:
        logger.warning("trends fetch failed for %r: %s", seed, exc)
        await update.message.reply_text(
            "⚠️ Google Trends didn't return data for that term (it may be too rare, "
            "or Trends is temporarily rate-limiting). Try again shortly."
        )
        return
    text = (
        f"*Trend summary for “{seed}”*\n"
        f"Direction (last 12 months): *{data['direction']}*\n"
        f"Average interest score: *{data['avg_interest']}*/100"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def related_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/related <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(f"🔁 Finding related searches for *{seed}*…", parse_mode=ParseMode.MARKDOWN)
    try:
        data = await asyncio.to_thread(get_trends_summary, seed)
    except Exception as exc:
        logger.warning("related fetch failed for %r: %s", seed, exc)
        await update.message.reply_text("⚠️ Couldn't fetch related searches right now. Try again shortly.")
        return
    text = (
        format_list(f"Top related to “{seed}”", data["top"])
        + "\n\n"
        + format_list(f"Rising related to “{seed}”", data["rising"])
    )
    await send_long_message(update, text)


async def full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = get_seed_arg(context)
    if not seed:
        await update.message.reply_text("Usage: `/full <seed keyword>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(
        f"⏳ Running a full keyword report for *{seed}* — this can take ~30-60s...",
        parse_mode=ParseMode.MARKDOWN,
    )

    alphabet_soup, questions, prepositions = await asyncio.gather(
        asyncio.to_thread(get_alphabet_soup, seed),
        asyncio.to_thread(get_question_keywords, seed),
        asyncio.to_thread(get_preposition_keywords, seed),
    )
    await send_long_message(update, format_list(f"Keyword suggestions for “{seed}”", list(alphabet_soup.keys())))
    await send_long_message(update, format_list(f"Question keywords for “{seed}”", list(questions.keys())))
    await send_long_message(update, format_list(f"Preposition keywords for “{seed}”", list(prepositions.keys())))

    try:
        trend_data = await asyncio.to_thread(get_trends_summary, seed)
        trend_text = (
            f"*Trend summary for “{seed}”*\n"
            f"Direction (12 mo): *{trend_data['direction']}*  |  "
            f"Avg interest: *{trend_data['avg_interest']}*/100\n\n"
            + format_list("Top related", trend_data["top"])
            + "\n\n"
            + format_list("Rising related", trend_data["rising"])
        )
        await send_long_message(update, trend_text)
    except Exception as exc:
        logger.warning("trends step of /full failed for %r: %s", seed, exc)
        await update.message.reply_text("⚠️ Trends data unavailable for this term right now.")


async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Unknown command. Send /help to see what I can do.")


def main() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("keywords", keywords_cmd))
    app.add_handler(CommandHandler("questions", questions_cmd))
    app.add_handler(CommandHandler("prepositions", prepositions_cmd))
    app.add_handler(CommandHandler("trends", trends_cmd))
    app.add_handler(CommandHandler("related", related_cmd))
    app.add_handler(CommandHandler("full", full_cmd))

    logger.info("Bot starting via long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
