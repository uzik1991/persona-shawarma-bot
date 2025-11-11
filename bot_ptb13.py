#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os, json, logging, datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (
    Updater, CallbackContext, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters
)

# ────────────────────────── CONFIG ──────────────────────────
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("Please set TELEGRAM_TOKEN env var.")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or "0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("shawarma-bot13")

def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")

# ───────────────────────── MENU / PRICES ─────────────────────
SHAWARMA_ITEMS = {
    "koko":   {"name": "Коко",   "price": 260},
    "disney": {"name": "Дісней", "price": 160},
}
ADDONS = {
    "onion": {"name": "Цибуля",     "price": 10},
    "mozz":  {"name": "Моцарелла",  "price": 20},
}
SIDES = {
    "sweet_fries": {"name": "Батат-фрі",     "price": 185, "note": "подається з трюфельним соусом"},
    "dips":        {"name": "Діпи",          "price": 150, "note": "подається з сирним соусом"},
    "falafel":     {"name": "Фалафель",      "price": 165, "note": "подається з хумусом"},
    "cheese_balls":{"name": "Сирні кульки",  "price": 140, "note": "подається з ягідним соусом"},
}
DESSERTS = {
    "pear_dorblu": {"name": "Торт Груша-Дорблю", "price": 160},
    "carrot":      {"name": "Торт Морквʼяний",    "price": 150},
    "brownie":     {"name": "Брауні",             "price": 130},
}
DRINKS = {
    "cola": {"name": "Кола",      "price": 70},
    "ayran":{"name": "Айран",     "price": 95},
    "capp": {"name": "Капучино",  "price": 120},
}

# ───────────────────────── ORDER SEQ ─────────────────────────
DATA_DIR = Path(__file__).parent
SEQ_FILE = DATA_DIR / "order_seq.json"

def _load_seq():
    if SEQ_FILE.exists():
        try:
            d = json.loads(SEQ_FILE.read_text(encoding="utf-8"))
            return d.get("date"), int(d.get("seq", 0))
        except Exception:
            pass
    return None, 0

def _save_seq(date, seq):
    SEQ_FILE.write_text(json.dumps({"date": date, "seq": seq}, ensure_ascii=False), encoding="utf-8")

def next_order_no() -> str:
    today = dt.datetime.now().strftime("%Y%m%d")
    last, seq = _load_seq()
    if last != today:
        seq = 0
    seq += 1
    _save_seq(today, seq)
    return f"T{today}-{seq:04d}"

# ───────────────────────── SESSION ──────────────────────────
@dataclass
class Session:
    history: List[str] = field(default_factory=list)
    delivery_method: Optional[str] = None  # 'delivery' / 'pickup'
    address: Optional[str] = None
    phone: Optional[str] = None
    comment: str = ""

    basket_shawarma: Dict[str, int] = field(default_factory=dict)
    basket_addons: Dict[str, int]   = field(default_factory=dict)
    basket_sides: Dict[str, int]    = field(default_factory=dict)
    basket_desserts: Dict[str, int] = field(default_factory=dict)
    basket_drinks: Dict[str, int]   = field(default_factory=dict)

    sel_shawarma: Set[str] = field(default_factory=set)
    sel_addons:   Set[str] = field(default_factory=set)
    sel_sides:    Set[str] = field(default_factory=set)
    sel_desserts: Set[str] = field(default_factory=set)
    sel_drinks:   Set[str] = field(default_factory=set)

    qty_sw_queue: List[str] = field(default_factory=list); qty_sw_index: int = 0
    qty_add_queue: List[str] = field(default_factory=list); qty_add_index: int = 0
    qty_sd_queue: List[str] = field(default_factory=list); qty_sd_index: int = 0
    qty_ds_queue: List[str] = field(default_factory=list); qty_ds_index: int = 0
    qty_dr_queue: List[str] = field(default_factory=list); qty_dr_index: int = 0

    awaiting: Optional[str] = None   # 'addr' | 'phone' | 'comment'
    current_order_no: Optional[str] = None

def get_session(ctx: CallbackContext) -> Session:
    if "session" not in ctx.user_data:
        ctx.user_data["session"] = Session()
    return ctx.user_data["session"]

# ───────────────────────── ORDERS REGISTRY (status + DM) ────
def ensure_globals(ctx: CallbackContext):
    ctx.bot_data.setdefault("orders", {})              # order_no -> {...}
    ctx.bot_data.setdefault("await_admin_dm", {})      # admin_id -> order_no
    ctx.bot_data.setdefault("await_user_dm", {})       # user_chat_id -> order_no

def ORDERS(ctx: CallbackContext) -> Dict[str, dict]:
    ensure_globals(ctx)
    return ctx.bot_data["orders"]

def set_admin_wait_dm(ctx: CallbackContext, admin_id: int, order_no: str):
    ensure_globals(ctx)
    ctx.bot_data["await_admin_dm"][admin_id] = order_no

def pop_admin_wait_dm(ctx: CallbackContext, admin_id: int) -> Optional[str]:
    ensure_globals(ctx)
    return ctx.bot_data["await_admin_dm"].pop(admin_id, None)

def set_user_wait_dm(ctx: CallbackContext, user_chat_id: int, order_no: str):
    ensure_globals(ctx)
    ctx.bot_data["await_user_dm"][user_chat_id] = order_no

def pop_user_wait_dm(ctx: CallbackContext, user_chat_id: int) -> Optional[str]:
    ensure_globals(ctx)
    return ctx.bot_data["await_user_dm"].pop(user_chat_id, None)

# ───────────────────────── UI HELPERS ───────────────────────
def _ack(update: Update):
    # Миттєво гасять «підсвітку» інлайн‑кнопки в клієнті
    try:
        update.callback_query.answer()
    except Exception:
        pass

# ───────────────────────── KEYBOARDS ────────────────────────
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🥙 Шаурма",  callback_data="nav:shawarma")],
        [InlineKeyboardButton("🍟 Сайди",   callback_data="nav:sides")],
        [InlineKeyboardButton("🍰 Десерти", callback_data="nav:desserts")],
        [InlineKeyboardButton("🥤 Напої",   callback_data="nav:drinks")],
        [InlineKeyboardButton("🧺 Кошик",   callback_data="cart:open")],
    ])

def kb_ship() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚚 Доставка",  callback_data="ship:delivery")],
        [InlineKeyboardButton("🏃‍♀️ Самовивіз", callback_data="ship:pickup")],
    ])

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")]])

def kb_check(options, selected: Set[str], scope: str, with_continue=True) -> InlineKeyboardMarkup:
    rows = []
    for oid, meta in options.items():
        label = f"{'☑' if oid in selected else '□'} {meta['name']} — {meta['price']} грн"
        rows.append([InlineKeyboardButton(label, callback_data=f"{scope}:toggle:{oid}")])
    if with_continue:
        rows.append([InlineKeyboardButton("Продовжити ▶️", callback_data=f"{scope}:continue")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")])
    return InlineKeyboardMarkup(rows)

def kb_qty(scope: str, target: str) -> InlineKeyboardMarkup:
    # Цифрова сітка лишається як є (комфорт швидкого вибору)
    rows = [
        [InlineKeyboardButton(str(n), callback_data=f"{scope}:qty:{target}:{n}") for n in (1,2,3)],
        [InlineKeyboardButton(str(n), callback_data=f"{scope}:qty:{target}:{n}") for n in (4,5,6)],
        [InlineKeyboardButton(str(n), callback_data=f"{scope}:qty:{target}:{n}") for n in (7,8,9)],
        [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
    ]
    return InlineKeyboardMarkup(rows)

def kb_yesno(scope: str) -> InlineKeyboardMarkup:
    # По одному на рядок
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Так", callback_data=f"{scope}:yes")],
        [InlineKeyboardButton("Ні",  callback_data=f"{scope}:no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
    ])

def kb_comment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустити", callback_data="comment:skip")],
        [InlineKeyboardButton("⬅️ Назад",   callback_data="nav:back")],
    ])

def kb_summary() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Підтвердити ✅", callback_data="order:confirm")],
        [InlineKeyboardButton("⬅️ Назад",       callback_data="nav:back")],
    ])

def kb_cart() -> InlineKeyboardMarkup:
    # Кожна важлива дія — окремий рядок
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("До меню",     callback_data="nav:home")],
        [InlineKeyboardButton("🗑️ Очистити", callback_data="cart:clear")],
        [InlineKeyboardButton("Підтвердити ✅", callback_data="order:confirm")],
    ])

def kb_admin_status(order_no: str) -> InlineKeyboardMarkup:
    # 1 кнопка = 1 рядок, щоб підсвічення займало майже всю ширину
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Прийняти 🟢",  callback_data=f"admin:{order_no}:accept")],
        [InlineKeyboardButton("Готуємо 👨‍🍳", callback_data=f"admin:{order_no}:cooking")],
        [InlineKeyboardButton("Курʼєр 🚴",    callback_data=f"admin:{order_no}:courier")],
        [InlineKeyboardButton("Готово ✅",    callback_data=f"admin:{order_no}:done")],
        [InlineKeyboardButton("✉️ Написати клієнту", callback_data=f"adminmsg:{order_no}")],
    ])

def kb_user_tracking(order_no: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Нове замовлення", callback_data="nav:restart")],
        [InlineKeyboardButton("✉️ Написати адміну", callback_data=f"usermsg:{order_no}")]
    ])

# ───────────────────────── TEXT HELPERS ─────────────────────
def money(n: int) -> str: return f"{n} грн"

def summarize(ses: Session) -> str:
    total = 0
    lines = ["Замовлення:"]

    for iid, qty in ses.basket_shawarma.items():
        meta = SHAWARMA_ITEMS[iid]; total += meta["price"] * qty
        lines.append(f"Шаурма {meta['name']} — {qty} шт")

    for sid, qty in ses.basket_sides.items():
        meta = SIDES[sid]; total += meta["price"] * qty
        lines.append(f"Сайд {meta['name']} — {qty} шт")

    for did, qty in ses.basket_desserts.items():
        meta = DESSERTS[did]; total += meta["price"] * qty
        lines.append(f"Десерт {meta['name']} — {qty} шт")

    for rid, qty in ses.basket_drinks.items():
        meta = DRINKS[rid]; total += meta["price"] * qty
        lines.append(f"Напій {meta['name']} — {qty} шт")

    if ses.basket_addons:
        lines += ["", "Додатки:"]
        for aid, qty in ses.basket_addons.items():
            meta = ADDONS[aid]; total += meta["price"] * qty
            lines.append(f"{meta['name']} — {qty} пор.")

    lines.append("")
    if ses.delivery_method:
        lines.append(f"Отримання: {'Доставка' if ses.delivery_method=='delivery' else 'Самовивіз'}")
    if ses.delivery_method == "delivery" and ses.address:
        lines.append(f"Адреса: {ses.address}")
    if ses.phone:
        lines.append(f"Телефон: {ses.phone}")
    if ses.comment:
        lines.append(f"Коментар: {ses.comment}")

    lines += ["", f"Ціна: {money(total)}"]

    order_no = ses.current_order_no or next_order_no()
    ses.current_order_no = order_no
    return "Номер замовлення: " + order_no + "\n\n" + "\n".join(lines)

def cart_text(ses: Session) -> str:
    lines = ["<b>Кошик</b>"]
    empty = True

    def add_group(title, items, catalog):
        nonlocal empty
        if items:
            empty = False
            lines.append(f"\n<b>{title}</b>")
            for iid, qty in items.items():
                meta = catalog[iid]
                lines.append(f"• {meta['name']} — {qty} шт")

    add_group("Шаурма",  ses.basket_shawarma, SHAWARMA_ITEMS)
    add_group("Сайди",   ses.basket_sides,    SIDES)
    add_group("Десерти", ses.basket_desserts, DESSERTS)
    add_group("Напої",   ses.basket_drinks,   DRINKS)

    if ses.basket_addons:
        lines.append(f"\n<b>Додатки</b>")
        for aid, qty in ses.basket_addons.items():
            lines.append(f"• {ADDONS[aid]['name']} — {qty} пор.")

    if empty:
        lines.append("\n(Порожньо)")
    lines.append("\nНатисніть «Підтвердити ✅» для оформлення або «До меню» для продовження.")
    return "\n".join(lines)

# ───────────────────────── RENDER HELPERS ───────────────────
def push_state(ses: Session, tag: str):
    if not ses.history or ses.history[-1] != tag:
        ses.history.append(tag)

def render_by_tag(update: Update, ctx: CallbackContext, tag: str):
    ses = get_session(ctx)
    mapping = {
        "delivery_choice": lambda: render_delivery(update, ctx, True),
        "addr_wait":       lambda: render_addr(update, ctx),
        "phone_wait":      lambda: render_phone(update, ctx, True),
        "home":            lambda: render_home(update, ctx, True),
        "shawarma_select": lambda: render_sw_select(update, ctx),
        "addons_yesno":    lambda: render_addons_yesno(update, ctx),
        "addons_select":   lambda: render_addons_select(update, ctx),
        "add_more":        lambda: render_add_more(update, ctx),
        "comment_wait":    lambda: render_comment_prompt(update, ctx),
        "summary":         lambda: render_summary(update, ctx),
        "sides_select":    lambda: render_generic_select(update, ctx, SIDES, ses.sel_sides, "sides", "Обери сайди (можна кілька):"),
        "desserts_select": lambda: render_generic_select(update, ctx, DESSERTS, ses.sel_desserts, "desserts", "Обери десерти (можна кілька):"),
        "drinks_select":   lambda: render_generic_select(update, ctx, DRINKS, ses.sel_drinks, "drinks", "Обери напої (можна кілька):"),
    }
    if tag.startswith("shawarma_qty"): return render_sw_qty(update, ctx)
    if tag.startswith("addons_qty"):   return render_addons_qty(update, ctx)
    if tag.startswith("sides_qty"):    return render_generic_qty(update, ctx, SIDES, ses.qty_sd_queue, "qty_sd_index", "sides", "Скільки")
    if tag.startswith("desserts_qty"): return render_generic_qty(update, ctx, DESSERTS, ses.qty_ds_queue, "qty_ds_index", "desserts", "Скільки")
    if tag.startswith("drinks_qty"):   return render_generic_qty(update, ctx, DRINKS, ses.qty_dr_queue, "qty_dr_index", "drinks", "Скільки")
    return mapping.get(tag, lambda: render_home(update, ctx, True))()

# ───────────────────────── RENDERS ──────────────────────────
def render_delivery(update: Update, ctx: CallbackContext, replace=False):
    ses = get_session(ctx)
    ses.history = []
    push_state(ses, "delivery_choice")
    user = update.effective_user
    text = f"Вітаю, {user.first_name}!\nОбери: доставка або самовивіз."
    send = update.callback_query.edit_message_text if replace and update.callback_query else update.effective_chat.send_message
    send(text, reply_markup=kb_ship(), disable_web_page_preview=True)

def render_addr(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    ses.awaiting = "addr"
    push_state(ses, "addr_wait")
    update.callback_query.edit_message_text("Введіть адресу доставки текстом:", reply_markup=kb_back())

def render_phone(update: Update, ctx: CallbackContext, replace=True):
    ses = get_session(ctx)
    ses.awaiting = "phone"
    push_state(ses, "phone_wait")
    f = update.callback_query.edit_message_text if replace and update.callback_query else update.effective_chat.send_message
    f("Введіть номер телефону:", reply_markup=kb_back())

def render_home(update: Update, ctx: CallbackContext, replace=False):
    ses = get_session(ctx)
    push_state(ses, "home")
    send = update.callback_query.edit_message_text if replace and update.callback_query else update.effective_chat.send_message
    send("Що бажаєте сьогодні?", reply_markup=kb_main())

def render_sw_select(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, "shawarma_select")
    markup = kb_check(SHAWARMA_ITEMS, ses.sel_shawarma, "shawarma")
    update.callback_query.edit_message_text("Оберіть шаурму (можна кілька):", reply_markup=markup)

def render_sw_qty(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, f"shawarma_qty:{ses.qty_sw_index}")
    item_id = ses.qty_sw_queue[ses.qty_sw_index]
    item = SHAWARMA_ITEMS[item_id]
    markup = kb_qty("shawarma", item_id)
    update.callback_query.edit_message_text(f"Скільки «{item['name']}»?", reply_markup=markup)

def render_addons_yesno(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, "addons_yesno")
    markup = kb_yesno("addons")
    update.callback_query.edit_message_text("Чи потрібно щось додати в шаурму?", reply_markup=markup)

def render_addons_select(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, "addons_select")
    markup = kb_check(ADDONS, ses.sel_addons, "addons")
    update.callback_query.edit_message_text("Оберіть додатки (можна кілька):", reply_markup=markup)

def render_addons_qty(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, f"addons_qty:{ses.qty_add_index}")
    aid = ses.qty_add_queue[ses.qty_add_index]
    addon = ADDONS[aid]
    markup = kb_qty("addons", aid)
    update.callback_query.edit_message_text(f"Скільки порцій «{addon['name']}»?", reply_markup=markup)

def render_add_more(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, "add_more")
    markup = kb_yesno("addmore")
    update.callback_query.edit_message_text("Додати щось ще до замовлення?", reply_markup=markup)

def render_comment_prompt(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    ses.awaiting = "comment"
    push_state(ses, "comment_wait")
    markup = kb_comment()
    update.callback_query.edit_message_text("Додати коментар? Надішліть текст або натисніть «Пропустити».",
                                            reply_markup=markup)

def render_summary(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    push_state(ses, "summary")
    markup = kb_summary()
    update.callback_query.edit_message_text(summarize(ses), reply_markup=markup, disable_web_page_preview=True)

def render_generic_select(update: Update, ctx: CallbackContext, options, selected, scope, title):
    ses = get_session(ctx)
    push_state(ses, f"{scope}_select")
    markup = kb_check(options, selected, scope)
    update.callback_query.edit_message_text(title, reply_markup=markup)

def render_generic_qty(update: Update, ctx: CallbackContext, options, queue, index_attr, scope, title_prefix):
    ses = get_session(ctx)
    idx = getattr(ses, index_attr)
    push_state(ses, f"{scope}_qty:{idx}")
    item_id = queue[idx]
    item = options[item_id]
    markup = kb_qty(scope, item_id)
    update.callback_query.edit_message_text(f"{title_prefix} «{item['name']}»?", reply_markup=markup)

# ───────────────────────── COMMANDS ─────────────────────────
def cmd_start(update: Update, ctx: CallbackContext):
    ctx.user_data["session"] = Session()
    render_delivery(update, ctx, False)

def cmd_help(update: Update, ctx: CallbackContext):
    text = (
        "<b>Допомога</b>\n"
        "Години: Пн–Нд 10:00–22:00\n"
        "Зона доставки: до 3 км від [адреси]\n"
        "Час: 30–60 хв\n"
        "Оплата: готівка/картка/посилання\n"
        "Контакт: +38 ХХХ ХХХ ХХ ХХ"
    )
    update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ───────────────────────── TEXT INPUTS ──────────────────────
def fallback_text(update: Update, ctx: CallbackContext):
    ensure_globals(ctx)

    # 1) Admin waiting to DM a client?
    if update.effective_user and update.effective_user.id == ADMIN_CHAT_ID:
        order_no = pop_admin_wait_dm(ctx, ADMIN_CHAT_ID)
        if order_no:
            reg = ORDERS(ctx).get(order_no)
            if reg and reg.get('user_chat_id'):
                ctx.bot.send_message(
                    reg['user_chat_id'],
                    f"📩 Повідомлення від адміністратора по {order_no}:\n\n{update.message.text}"
                )
                update.message.reply_text("Надіслано клієнту ✅")
                return

    # 2) User waiting to DM admin?
    uchat = update.effective_chat.id
    order_no = pop_user_wait_dm(ctx, uchat)
    if order_no and ADMIN_CHAT_ID:
        u = update.effective_user
        ctx.bot.send_message(
            ADMIN_CHAT_ID,
            f"📨 Повідомлення від клієнта по {order_no}\n"
            f"👤 {u.full_name} (id {u.id})\n\n{update.message.text}"
        )
        update.message.reply_text("Надіслано адміну ✅")
        return

    # 3) Regular awaited inputs
    ses = get_session(ctx)
    txt = (update.message.text or "").strip()

    if ses.awaiting == "addr":
        ses.address = txt
        ses.awaiting = None
        render_phone(update, ctx, False)
        return

    if ses.awaiting == "phone":
        ses.phone = txt
        ses.awaiting = None
        render_home(update, ctx, False)
        return

    if ses.awaiting == "comment":
        ses.comment = txt
        ses.awaiting = None
        update.message.reply_text("Коментар додано ✅")
        update.effective_chat.send_message(summarize(ses), reply_markup=kb_summary(), disable_web_page_preview=True)
        push_state(ses, "summary")
        return

    update.message.reply_text("Надішліть /start для меню або користуйтесь кнопками.")

# ───────────────────────── CALLBACKS ────────────────────────
def on_shipping(update: Update, ctx: CallbackContext):
    _ack(update)
    data = update.callback_query.data.split(":", 1)[1]
    ses = get_session(ctx)
    if data == "delivery":
        ses.delivery_method = "delivery"
        render_addr(update, ctx)
    elif data == "pickup":
        ses.delivery_method = "pickup"
        render_phone(update, ctx)

def on_nav(update: Update, ctx: CallbackContext):
    _ack(update)
    data = update.callback_query.data.split(":", 1)[1]
    ses = get_session(ctx)

    if data == "restart":
        ctx.user_data["session"] = Session()
        return render_delivery(update, ctx, True)

    if data == "home":
        return render_home(update, ctx, True)

    if data == "shawarma":
        ses.sel_shawarma = set(); ses.qty_sw_queue = []; ses.qty_sw_index = 0
        return render_sw_select(update, ctx)

    if data == "sides":
        ses.sel_sides = set(); ses.qty_sd_queue = []; ses.qty_sd_index = 0
        return render_generic_select(update, ctx, SIDES, ses.sel_sides, "sides", "Обери сайди (можна кілька):")

    if data == "desserts":
        ses.sel_desserts = set(); ses.qty_ds_queue = []; ses.qty_ds_index = 0
        return render_generic_select(update, ctx, DESSERTS, ses.sel_desserts, "desserts", "Обери десерти (можна кілька):")

    if data == "drinks":
        ses.sel_drinks = set(); ses.qty_dr_queue = []; ses.qty_dr_index = 0
        return render_generic_select(update, ctx, DRINKS, ses.sel_drinks, "drinks", "Обери напої (можна кілька):")

    if data == "back":
        if not ses.history:
            return update.callback_query.answer("Назад недоступно.")
        ses.history.pop()
        if not ses.history:
            return render_delivery(update, ctx, True)
        prev = ses.history[-1]
        return render_by_tag(update, ctx, prev)

def on_sw(update: Update, ctx: CallbackContext):
    _ack(update)
    data  = update.callback_query.data.split(":", 1)[1]
    ses   = get_session(ctx)
    parts = data.split(":")
    action = parts[0]

    if action == "toggle":
        oid = parts[1]
        if oid in ses.sel_shawarma: ses.sel_shawarma.remove(oid)
        else: ses.sel_shawarma.add(oid)
        markup = kb_check(SHAWARMA_ITEMS, ses.sel_shawarma, "shawarma")
        return update.callback_query.edit_message_reply_markup(markup)

    if action == "continue":
        if not ses.sel_shawarma:
            return update.callback_query.answer("Виберіть хоча б одну позицію.", show_alert=True)
        ses.qty_sw_queue = list(ses.sel_shawarma); ses.qty_sw_index = 0
        return render_sw_qty(update, ctx)

    if action == "qty":
        item_id = parts[1]; qty = int(parts[2])
        ses.basket_shawarma[item_id] = ses.basket_shawarma.get(item_id, 0) + qty
        if ses.qty_sw_index + 1 < len(ses.qty_sw_queue):
            ses.qty_sw_index += 1; return render_sw_qty(update, ctx)
        else:
            return render_addons_yesno(update, ctx)

def on_addons(update: Update, ctx: CallbackContext):
    _ack(update)
    data  = update.callback_query.data.split(":", 1)[1]
    ses   = get_session(ctx)
    parts = data.split(":")
    action = parts[0]

    if action == "yes":
        ses.sel_addons = set(); ses.qty_add_queue = []; ses.qty_add_index = 0
        return render_addons_select(update, ctx)

    if action == "no":
        return render_add_more(update, ctx)

    if action == "toggle":
        aid = parts[1]
        if aid in ses.sel_addons: ses.sel_addons.remove(aid)
        else: ses.sel_addons.add(aid)
        markup = kb_check(ADDONS, ses.sel_addons, "addons")
        return update.callback_query.edit_message_reply_markup(markup)

    if action == "continue":
        if not ses.sel_addons:
            return render_add_more(update, ctx)
        ses.qty_add_queue = list(ses.sel_addons); ses.qty_add_index = 0
        return render_addons_qty(update, ctx)

    if action == "qty":
        aid = parts[1]; qty = int(parts[2])
        ses.basket_addons[aid] = ses.basket_addons.get(aid, 0) + qty
        if ses.qty_add_index + 1 < len(ses.qty_add_queue):
            ses.qty_add_index += 1; return render_addons_qty(update, ctx)
        else:
            return render_add_more(update, ctx)

def on_comment(update: Update, ctx: CallbackContext):
    _ack(update)
    action = update.callback_query.data.split(":", 1)[1]
    ses = get_session(ctx)
    if action == "skip":
        ses.comment = ""
        return render_summary(update, ctx)

def on_addmore(update: Update, ctx: CallbackContext):
    _ack(update)
    data = update.callback_query.data.split(":", 1)[1]
    if data == "yes":
        return render_home(update, ctx, True)
    else:
        return render_comment_prompt(update, ctx)

def on_cart(update: Update, ctx: CallbackContext):
    _ack(update)
    q = update.callback_query.data.split(":", 1)[1]
    ses = get_session(ctx)

    if q == "open":
        markup = kb_cart()
        return update.callback_query.edit_message_text(cart_text(ses), parse_mode=ParseMode.HTML, reply_markup=markup)

    if q == "clear":
        ses.basket_shawarma.clear(); ses.basket_addons.clear()
        ses.basket_sides.clear();    ses.basket_desserts.clear(); ses.basket_drinks.clear()
        return update.callback_query.edit_message_text(
            "Кошик очищено 🗑️",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("До меню", callback_data="nav:home")]])
        )

def finalize_order(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    order_no = ses.current_order_no or next_order_no()
    ses.current_order_no = order_no

    summary_text = summarize(ses)
    ts = now_str()

    # 1) Admin panel message
    admin_msg_id = None
    if ADMIN_CHAT_ID:
        u = update.effective_user
        client_line = (f"👤 Клієнт: (тест із адмін-акаунта) id {u.id}"
                       if u.id == ADMIN_CHAT_ID else f"👤 Клієнт: {u.full_name} (id {u.id})")
        m = ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Нове замовлення {order_no}\n🕒 {ts}\n{client_line}\n\n{summary_text}\n\nСтатус: 🟡 Нове — {ts}",
            reply_markup=kb_admin_status(order_no)
        )
        admin_msg_id = m.message_id

    # 2) Customer tracking message (with reply-to-admin button)
    user_msg = update.callback_query.message.edit_text(
        f"{summary_text}\n\nСтатус: 🟡 Нове — {ts}",
        reply_markup=kb_user_tracking(order_no)
    )

    # 3) Register order
    reg = ORDERS(ctx)
    reg[order_no] = {
        "user_chat_id": update.effective_chat.id,
        "user_status_msg_id": user_msg.message_id,
        "admin_msg_id": admin_msg_id or 0,
        "summary_text": summary_text,
    }

def on_order(update: Update, ctx: CallbackContext):
    _ack(update)
    if update.callback_query.data == "order:confirm":
        return finalize_order(update, ctx)

def on_generic(update: Update, ctx: CallbackContext, options, selected: Set[str],
               queue_attr: str, index_attr: str, basket: Dict[str, int], scope: str):
    _ack(update)
    data  = update.callback_query.data.split(":", 1)[1]
    ses   = get_session(ctx)
    parts = data.split(":")
    action = parts[0]

    if action == "toggle":
        oid = parts[1]
        if oid in selected: selected.remove(oid)
        else: selected.add(oid)
        markup = kb_check(options, selected, scope)
        return update.callback_query.edit_message_reply_markup(markup)

    if action == "continue":
        if not selected:
            return update.callback_query.answer("Виберіть хоча б одну позицію.", show_alert=True)
        setattr(ses, queue_attr, list(selected))
        setattr(ses, index_attr, 0)
        if scope == "sides":
            return render_generic_qty(update, ctx, options, ses.qty_sd_queue, "qty_sd_index", "sides", "Скільки")
        if scope == "desserts":
            return render_generic_qty(update, ctx, options, ses.qty_ds_queue, "qty_ds_index", "desserts", "Скільки")
        if scope == "drinks":
            return render_generic_qty(update, ctx, options, ses.qty_dr_queue, "qty_dr_index", "drinks", "Скільки")

    if action == "qty":
        item_id = parts[1]; qty = int(parts[2])
        basket[item_id] = basket.get(item_id, 0) + qty
        idx = getattr(ses, index_attr); queue = getattr(ses, queue_attr)
        if idx + 1 < len(queue):
            setattr(ses, index_attr, idx + 1)
            if scope == "sides":
                return render_generic_qty(update, ctx, options, ses.qty_sd_queue, "qty_sd_index", "sides", "Скільки")
            elif scope == "desserts":
                return render_generic_qty(update, ctx, options, ses.qty_ds_queue, "qty_ds_index", "desserts", "Скільки")
            elif scope == "drinks":
                return render_generic_qty(update, ctx, options, ses.qty_dr_queue, "qty_dr_index", "drinks", "Скільки")
        else:
            return render_add_more(update, ctx)

def on_sides(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    return on_generic(update, ctx, SIDES, ses.sel_sides, "qty_sd_queue", "qty_sd_index", ses.basket_sides, "sides")

def on_desserts(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    return on_generic(update, ctx, DESSERTS, ses.sel_desserts, "qty_ds_queue", "qty_ds_index", ses.basket_desserts, "desserts")

def on_drinks(update: Update, ctx: CallbackContext):
    ses = get_session(ctx)
    return on_generic(update, ctx, DRINKS, ses.sel_drinks, "qty_dr_queue", "qty_dr_index", ses.basket_drinks, "drinks")

def on_admin_status(update: Update, ctx: CallbackContext):
    _ack(update)
    if update.effective_user.id != ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)

    _, order_no, action = update.callback_query.data.split(":", 2)
    status_map = {"accept":"🟢 Прийнято","cooking":"👨‍🍳 Готуємо","courier":"🚴 Курʼєр в дорозі","done":"✅ Готово"}
    status = status_map.get(action, "🟡 Нове")
    ts = now_str()

    # Update admin panel text and keep buttons
    base = update.callback_query.message.text.split("\n\nСтатус:", 1)[0]
    update.callback_query.edit_message_text(
        base + f"\n\nСтатус: {status} — {ts}",
        reply_markup=kb_admin_status(order_no)
    )

    # Notify / update the user
    order_reg = ORDERS(ctx).get(order_no)
    if order_reg and order_reg.get("user_chat_id") and order_reg.get("user_status_msg_id"):
        # edit customer's tracking message
        try:
            summ = order_reg.get("summary_text", "(замовлення)")
            ctx.bot.edit_message_text(
                chat_id=order_reg["user_chat_id"],
                message_id=order_reg["user_status_msg_id"],
                text=f"{summ}\n\nСтатус: {status} — {ts}",
                reply_markup=kb_user_tracking(order_no)
            )
        except Exception as e:
            log.warning("Failed to edit user status message: %s", e)
        # send separate notification message (mask + timestamp)
        try:
            ctx.bot.send_message(
                chat_id=order_reg["user_chat_id"],
                text=f"Статус вашого замовлення змінено на: {status} — {ts}"
            )
        except Exception as e:
            log.warning("Failed to send separate status message: %s", e)

def on_admin_msg(update: Update, ctx: CallbackContext):
    _ack(update)
    if update.effective_user.id != ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)

    _, order_no = update.callback_query.data.split(":", 1)
    set_admin_wait_dm(ctx, ADMIN_CHAT_ID, order_no)
    update.callback_query.answer("Напишіть текст повідомлення для клієнта…")
    update.callback_query.edit_message_reply_markup(kb_admin_status(order_no))

def on_user_msg(update: Update, ctx: CallbackContext):
    _ack(update)
    # user clicked "write to admin"
    _, order_no = update.callback_query.data.split(":", 1)
    set_user_wait_dm(ctx, update.effective_chat.id, order_no)
    update.callback_query.answer("Напишіть повідомлення адміну…")
    update.callback_query.edit_message_reply_markup(kb_user_tracking(order_no))

# ───────────────────────── MAIN ─────────────────────────────
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help",  cmd_help))

    dp.add_handler(CallbackQueryHandler(on_shipping, pattern=r"^ship:"))
    dp.add_handler(CallbackQueryHandler(on_nav,      pattern=r"^nav:"))
    dp.add_handler(CallbackQueryHandler(on_sw,       pattern=r"^shawarma:"))
    dp.add_handler(CallbackQueryHandler(on_addons,   pattern=r"^addons:"))
    dp.add_handler(CallbackQueryHandler(on_comment,  pattern=r"^comment:"))
    dp.add_handler(CallbackQueryHandler(on_addmore,  pattern=r"^addmore:"))
    dp.add_handler(CallbackQueryHandler(on_cart,     pattern=r"^cart:"))
    dp.add_handler(CallbackQueryHandler(on_order,    pattern=r"^order:confirm$"))
    dp.add_handler(CallbackQueryHandler(on_sides,    pattern=r"^sides:"))
    dp.add_handler(CallbackQueryHandler(on_desserts, pattern=r"^desserts:"))
    dp.add_handler(CallbackQueryHandler(on_drinks,   pattern=r"^drinks:"))
    dp.add_handler(CallbackQueryHandler(on_admin_status, pattern=r"^admin:"))
    dp.add_handler(CallbackQueryHandler(on_admin_msg,    pattern=r"^adminmsg:"))
    dp.add_handler(CallbackQueryHandler(on_user_msg,     pattern=r"^usermsg:"))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, fallback_text))

    log.info("Starting bot polling (PTB 13.x, status+DM, timestamps, 1btn/row)...")
    updater.start_polling(drop_pending_updates=True)
    updater.idle()

if __name__ == "__main__":
    main()
