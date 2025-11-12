#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os, logging, re, datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, ParseMode
)
from telegram.ext import (
    Updater, CallbackContext, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters
)

# ------------ ENV ------------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("Set TELEGRAM_TOKEN env var")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or "0")
DONE_STICKER_FILE_ID = os.environ.get("DONE_STICKER_FILE_ID", "").strip()

# ------------ LOG ------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("shawarma-bot13")

def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")

# ------------ DATA ------------
SHAWARMA = {"koko":{"name":"Коко","price":260},"disney":{"name":"Дісней","price":160}}
ADDONS   = {"onion":{"name":"Цибуля","price":10},"mozz":{"name":"Моцарелла","price":20}}
SIDES    = {
    "sweet_fries":{"name":"Батат-фрі","price":185,"note":"подається з трюфельним соусом"},
    "dips":{"name":"Діпи","price":150,"note":"подається з сирним соусом"},
    "falafel":{"name":"Фалафель","price":165,"note":"подається з хумусом"},
    "cheese_balls":{"name":"Сирні кульки","price":140,"note":"подається з ягідним соусом"}
}
DESSERTS = {"pear_dorblu":{"name":"Торт Груша-Дорблю","price":160},
            "carrot":{"name":"Торт Морквʼяний","price":150},
            "brownie":{"name":"Брауні","price":130}}
DRINKS   = {"cola":{"name":"Кола","price":70},"ayran":{"name":"Айран","price":95},"capp":{"name":"Капучино","price":120}}

# ------------ ORDER SEQ ------------
SEQ_PATH="order_seq.json"
def next_order_no() -> str:
    today = dt.datetime.now().strftime("%Y%m%d")
    seq = 0
    try:
        if os.path.exists(SEQ_PATH):
            import json
            with open(SEQ_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date")==today:
                seq = int(d.get("seq",0))
    except Exception:
        pass
    seq += 1
    import json
    with open(SEQ_PATH, "w", encoding="utf-8") as f:
        json.dump({"date":today,"seq":seq}, f)
    return f"T{today}-{seq:04d}"

# ------------ SESSION ------------
@dataclass
class Session:
    history: List[str]=field(default_factory=list)
    awaiting: Optional[str]=None
    delivery: Optional[str]=None
    address: Optional[str]=None
    phone: Optional[str]=None
    comment: str=""
    sel_sw:Set[str]=field(default_factory=set); q_sw:List[str]=field(default_factory=list); i_sw:int=0; b_sw:Dict[str,int]=field(default_factory=dict)
    sel_add:Set[str]=field(default_factory=set); q_add:List[str]=field(default_factory=list); i_add:int=0; b_add:Dict[str,int]=field(default_factory=dict)
    sel_sd:Set[str]=field(default_factory=set); q_sd:List[str]=field(default_factory=list); i_sd:int=0; b_sd:Dict[str,int]=field(default_factory=dict)
    sel_ds:Set[str]=field(default_factory=set); q_ds:List[str]=field(default_factory=list); i_ds:int=0; b_ds:Dict[str,int]=field(default_factory=dict)
    sel_dr:Set[str]=field(default_factory=set); q_dr:List[str]=field(default_factory=list); i_dr:int=0; b_dr:Dict[str,int]=field(default_factory=dict)
    order_no: Optional[str]=None

# helpers for state in PTB13
def S(ctx: CallbackContext) -> Session:
    if "S" not in ctx.user_data:
        ctx.user_data["S"]=Session()
    return ctx.user_data["S"]

def ensure_globals(ctx: CallbackContext):
    bd = ctx.bot_data
    bd.setdefault("orders",{})
    bd.setdefault("admin_chat_for",{})
    bd.setdefault("user_chat_for",{})

def ORD(ctx): ensure_globals(ctx); return ctx.bot_data["orders"]
def ADMIN_FOR(ctx): ensure_globals(ctx); return ctx.bot_data["admin_chat_for"]
def USER_FOR(ctx): ensure_globals(ctx); return ctx.bot_data["user_chat_for"]

# ------------ UI ------------
SHIP_DELIVERY="🚚 Доставка"
SHIP_PICKUP="🏃‍♀️ Самовивіз"

def kb_ship_inline()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(SHIP_DELIVERY,"ship:delivery")],
                                 [InlineKeyboardButton(SHIP_PICKUP,"ship:pickup")]])

def kb_ship_reply()->ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[SHIP_DELIVERY, SHIP_PICKUP]], resize_keyboard=True, one_time_keyboard=True)

def kb_main()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌯 Шаурма","nav:shawarma")],
        [InlineKeyboardButton("🍟 Сайди","nav:sides")],
        [InlineKeyboardButton("🍰 Десерти","nav:desserts")],
        [InlineKeyboardButton("🥤 Напої","nav:drinks")],
        [InlineKeyboardButton("🧺 Кошик","cart:open")],
    ])

def kb_back()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад","nav:back")]])

def kb_check(options:Dict, selected:Set[str], scope:str, cont=True)->InlineKeyboardMarkup:
    rows=[]
    for oid,meta in options.items():
        mark="☑" if oid in selected else "□"
        label=f"{mark} {meta['name']} — {meta['price']} грн"
        if meta.get("note"): label+=f" ({meta['note']})"
        rows.append([InlineKeyboardButton(label, f"{scope}:toggle:{oid}")])
    if cont: rows.append([InlineKeyboardButton("Продовжити ▶️", f"{scope}:continue")])
    rows.append([InlineKeyboardButton("⬅️ Назад","nav:back")])
    return InlineKeyboardMarkup(rows)

def kb_qty(scope,item_id):
    rows=[[InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (1,2,3)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (4,5,6)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (7,8,9)],
          [InlineKeyboardButton("⬅️ Назад","nav:back")]]
    return InlineKeyboardMarkup(rows)

def kb_yesno(tag):
    return InlineKeyboardMarkup([[InlineKeyboardButton("Так",f"{tag}:yes")],
                                 [InlineKeyboardButton("Ні",f"{tag}:no")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])

def kb_comment():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустити","comment:skip")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])

def kb_summary():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Підтвердити ✅","order:confirm")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])

def kb_admin(order_no, chat_on:bool=False):
    rows = [
        [InlineKeyboardButton("Прийняти 🟢",f"admin:{order_no}:accept")],
        [InlineKeyboardButton("Готуємо 👨‍🍳",f"admin:{order_no}:cooking")],
        [InlineKeyboardButton("Курʼєр 🚴",f"admin:{order_no}:courier")],
        [InlineKeyboardButton("Готово ✅",f"admin:{order_no}:done")],
        [InlineKeyboardButton("✉️ Написати клієнту", f"adminmsg:{order_no}")]
    ]
    if chat_on:
        rows.append([InlineKeyboardButton("⛔ Завершити чат", f"adminchatend:{order_no}")])
    return InlineKeyboardMarkup(rows)

def kb_user(order_no, chat_on:bool=False):
    rows = [
        [InlineKeyboardButton("🆕 Нове замовлення","nav:restart")],
        [InlineKeyboardButton("✉️ Написати адміну", f"usermsg:{order_no}")]
    ]
    if chat_on:
        rows.append([InlineKeyboardButton("⛔ Завершити чат", f"userchatend:{order_no}")])
    return InlineKeyboardMarkup(rows)

def push(s:Session, tag:str):
    if not s.history or s.history[-1]!=tag:
        s.history.append(tag)

# ------------ FLOW RENDERERS ------------
def send_delivery_screen(ctx:CallbackContext, chat_id:int):
    s=S(ctx)
    s.history.clear()
    s.awaiting="ship"
    # ВАЖЛИВО: відобразити і inline, і reply-клавіатуру, щоб точно побачили кнопки.
    ctx.bot.send_message(chat_id, "Вітаю! Оберіть спосіб отримання:", reply_markup=kb_ship_inline())
    ctx.bot.send_message(chat_id, "Або натисніть нижні кнопки ⬇️", reply_markup=kb_ship_reply())
    push(s,"delivery")

def render_addr(update:Update, ctx:CallbackContext):
    s=S(ctx); s.awaiting="addr"; push(s,"addr")
    ctx.bot.send_message(update.effective_chat.id, "Введіть адресу доставки:", reply_markup=ReplyKeyboardRemove())
    ctx.bot.send_message(update.effective_chat.id, " ", reply_markup=kb_back())

def _digits(t:str)->str: return "".join(ch for ch in t if ch.isdigit())

def format_phone_mask(text:str)->Optional[str]:
    """Гнучке форматування у +38 (0XX)-XXX-XX-XX якщо вхід схожий на укр. номер."""
    d=_digits(text)
    # Приклади, які приймаємо й форматуємо: 38067..., 067..., 67...
    if d.startswith("380"): d=d[2:]
    if len(d)==10 and d[0]=="0":
        return f"+38 ({d[0:3]})-{d[3:6]}-{d[6:8]}-{d[8:10]}"
    return None

PHONE_RE = re.compile(r'^\+38\s*\(0\d{2}\)-\d{3}-\d{2}-\d{2}$')

def render_phone(update:Update, ctx:CallbackContext):
    s=S(ctx); s.awaiting="phone"; push(s,"phone")
    ctx.bot.send_message(
        update.effective_chat.id,
        "Введіть номер у форматі:\n<b>+38 (0XX)-XXX-XX-XX</b>\nПриклад: +38 (067)-123-45-67",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_back()
    )

def render_home(update:Update, ctx:CallbackContext):
    s=S(ctx); push(s,"home")
    ctx.bot.send_message(update.effective_chat.id, "Що бажаєте сьогодні?", reply_markup=kb_main())

def render_select(update:Update, ctx:CallbackContext, scope:str, selected:Set[str], data:Dict):
    push(S(ctx), f"{scope}_select")
    ctx.bot.send_message(update.effective_chat.id, "Оберіть позиції (можна кілька):", reply_markup=kb_check(data, selected, scope))

def render_qty(update:Update, ctx:CallbackContext, scope:str, seq:List[str], idx:int):
    push(S(ctx), f"{scope}_qty:{idx}")
    mapping={"sw":SHAWARMA,"add":ADDONS,"sd":SIDES,"ds":DESSERTS,"dr":DRINKS}
    item_id = seq[idx]; item = mapping[scope][item_id]
    ctx.bot.send_message(update.effective_chat.id, f"Скільки «{item['name']}»?", reply_markup=kb_qty(scope,item_id))

def render_comment(update:Update, ctx:CallbackContext):
    s=S(ctx); s.awaiting="comment"; push(s,"comment")
    ctx.bot.send_message(update.effective_chat.id, "Додати коментар? Надішліть текст або «Пропустити».", reply_markup=ReplyKeyboardRemove())
    ctx.bot.send_message(update.effective_chat.id, " ", reply_markup=kb_comment())

def summarize(s:Session)->str:
    total=0; lines=["Замовлення:"]
    for k,v in s.b_sw.items(): lines.append(f"Шаурма {SHAWARMA[k]['name']} — {v} шт"); total+=SHAWARMA[k]['price']*v
    for k,v in s.b_sd.items(): lines.append(f"{SIDES[k]['name']} — {v} шт"); total+=SIDES[k]['price']*v
    for k,v in s.b_ds.items(): lines.append(f"{DESSERTS[k]['name']} — {v} шт"); total+=DESSERTS[k]['price']*v
    for k,v in s.b_dr.items(): lines.append(f"{DRINKS[k]['name']} — {v} шт"); total+=DRINKS[k]['price']*v
    if s.b_add:
        lines.append(""); lines.append("Додатки:")
        for k,v in s.b_add.items(): lines.append(f"{ADDONS[k]['name']} — {v} пор."); total+=ADDONS[k]['price']*v
    if s.delivery: lines.append(""); lines.append(f"Отримання: {'Доставка' if s.delivery=='delivery' else 'Самовивіз'}")
    if s.delivery=='delivery' and s.address: lines.append(f"Адреса: {s.address}")
    if s.phone: lines.append(f"Телефон: {s.phone}")
    if s.comment: lines.append(f"Коментар: {s.comment}")
    lines.append(""); lines.append(f"Ціна: {total} грн")
    s.order_no = s.order_no or next_order_no()
    return "Номер замовлення: "+s.order_no+"\n\n" + "\n".join(lines)

def render_summary(update:Update, ctx:CallbackContext):
    s=S(ctx); push(s,"summary")
    ctx.bot.send_message(update.effective_chat.id, summarize(s), reply_markup=kb_summary(), disable_web_page_preview=True)

# ------------ HANDLERS ------------
def cmd_start(update:Update, ctx:CallbackContext):
    # Повний ресет користувача + вихід з режиму чату
    ensure_globals(ctx)
    uid = update.effective_chat.id
    if USER_FOR(ctx).get(uid): USER_FOR(ctx).pop(uid, None)
    ctx.user_data["S"]=Session()
    u=update.effective_user
    ctx.bot.send_message(update.effective_chat.id, f"Вітаю, {u.full_name}! 😊")
    send_delivery_screen(ctx, update.effective_chat.id)

def cmd_reset(update:Update, ctx:CallbackContext):
    return cmd_start(update, ctx)

def cmd_menu(update:Update, ctx:CallbackContext):
    send_delivery_screen(ctx, update.effective_chat.id)

def cmd_ping(update:Update, ctx:CallbackContext):
    ctx.bot.send_message(update.effective_chat.id, "pong")

def ack(update:Update):
    try: update.callback_query.answer()
    except Exception: pass

def on_ship(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx)
    data = update.callback_query.data
    if data=="ship:delivery":
        s.delivery="delivery"; s.awaiting=None; render_addr(update,ctx)
    elif data=="ship:pickup":
        s.delivery="pickup"; s.awaiting=None; render_phone(update,ctx)

def on_nav(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx); data=update.callback_query.data.split(":",1)[1]
    if data=="back":
        if not s.history: return send_delivery_screen(ctx, update.effective_chat.id)
        s.history.pop()
        if not s.history: return send_delivery_screen(ctx, update.effective_chat.id)
        tag=s.history[-1]
        if tag=="delivery": return send_delivery_screen(ctx, update.effective_chat.id)
        if tag=="addr": return render_addr(update,ctx)
        if tag=="phone": return render_phone(update,ctx)
        if tag=="home": return render_home(update,ctx)
        if tag.endswith("_select"):
            scope=tag.split("_")[0]
            mapping={"sw":SHAWARMA,"sd":SIDES,"ds":DESSERTS,"dr":DRINKS,"add":ADDONS}
            sel={"sw":s.sel_sw,"sd":s.sel_sd,"ds":s.sel_ds,"dr":s.sel_dr,"add":s.sel_add}[scope]
            return render_select(update,ctx,scope,sel,mapping[scope])
        if "_qty:" in tag:
            scope=tag.split("_qty:")[0]
            mapping={"sw":SHAWARMA,"sd":SIDES,"ds":DESSERTS,"dr":DRINKS,"add":ADDONS}
            sel={"sw":s.sel_sw,"sd":s.sel_sd,"ds":s.sel_ds,"dr":s.sel_dr,"add":s.sel_add}[scope]
            return render_select(update,ctx,scope,sel,mapping[scope])
        if tag=="comment": return render_comment(update,ctx)
        if tag=="summary": return render_summary(update,ctx)
        return render_home(update,ctx)
    if data=="restart":
        ctx.user_data["S"]=Session(); return send_delivery_screen(ctx, update.effective_chat.id)
    if data=="shawarma":
        s.sel_sw.clear(); s.q_sw.clear(); s.i_sw=0; return render_select(update,ctx,"sw", s.sel_sw, SHAWARMA)
    if data=="sides":
        s.sel_sd.clear(); s.q_sd.clear(); s.i_sd=0; return render_select(update,ctx,"sd", s.sel_sd, SIDES)
    if data=="desserts":
        s.sel_ds.clear(); s.q_ds.clear(); s.i_ds=0; return render_select(update,ctx,"ds", s.sel_ds, DESSERTS)
    if data=="drinks":
        s.sel_dr.clear(); s.q_dr.clear(); s.i_dr=0; return render_select(update,ctx,"dr", s.sel_dr, DRINKS)

def on_group(update:Update, ctx:CallbackContext, scope:str, sel:Set[str], seq:List[str], idx_attr:str, bucket:Dict[str,int], data:Dict[str,Dict], after_all):
    ack(update); s=S(ctx); _,action,*rest = update.callback_query.data.split(":")
    if action=="toggle":
        oid=rest[0]
        if oid in sel: sel.remove(oid)
        else: sel.add(oid)
        return update.callback_query.edit_message_reply_markup(kb_check(data,sel,scope))
    if action=="continue":
        if not sel:
            if scope=="add": return after_all()
            return update.callback_query.answer("Виберіть хоча б одну позицію.", show_alert=True)
        seq[:] = list(sel)
        setattr(s, idx_attr, 0)
        push(s, f"{scope}_select")
        return render_qty(update,ctx,scope,seq, getattr(s, idx_attr))
    if action=="qty":
        iid,qty = rest[0], int(rest[1])
        bucket[iid]=bucket.get(iid,0)+qty
        cur_i = getattr(s, idx_attr)
        if cur_i+1 < len(seq):
            setattr(s, idx_attr, cur_i+1)
            return render_qty(update,ctx,scope,seq, getattr(s, idx_attr))
        return after_all()

def on_sw(update:Update, ctx:CallbackContext):
    def after():
        push(S(ctx),"add_yesno")
        return ctx.bot.send_message(update.effective_chat.id, "Чи потрібно щось додати в шаурму?", reply_markup=kb_yesno("add"))
    return on_group(update,ctx,"sw", S(ctx).sel_sw, S(ctx).q_sw, "i_sw", S(ctx).b_sw, SHAWARMA, after)

def on_add_yesno(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx); _,ans=update.callback_query.data.split(":")
    push(s,"add_yesno")
    if ans=="yes":
        s.sel_add.clear(); s.q_add.clear(); s.i_add=0
        return render_select(update,ctx,"add", s.sel_add, ADDONS)
    return render_comment(update,ctx)

def on_add(update:Update, ctx:CallbackContext):
    return on_group(update,ctx,"add", S(ctx).sel_add, S(ctx).q_add, "i_add", S(ctx).b_add, ADDONS, lambda: render_comment(update,ctx))

def on_sd(update:Update, ctx:CallbackContext):
    return on_group(update,ctx,"sd", S(ctx).sel_sd, S(ctx).q_sd, "i_sd", S(ctx).b_sd, SIDES, lambda: render_home(update,ctx))

def on_ds(update:Update, ctx:CallbackContext):
    return on_group(update,ctx,"ds", S(ctx).sel_ds, S(ctx).q_ds, "i_ds", S(ctx).b_ds, DESSERTS, lambda: render_home(update,ctx))

def on_dr(update:Update, ctx:CallbackContext):
    return on_group(update,ctx,"dr", S(ctx).sel_dr, S(ctx).q_dr, "i_dr", S(ctx).b_dr, DRINKS, lambda: render_home(update,ctx))

def on_cart(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx)
    update.callback_query.edit_message_text(summarize(s), reply_markup=kb_summary(), disable_web_page_preview=True)
    push(s,"summary")

def on_order(update:Update, ctx:CallbackContext):
    ack(update)
    if update.callback_query.data=="order:confirm":
        s=S(ctx); o=s.order_no or next_order_no(); s.order_no=o; ts=now_str(); summ=summarize(s)
        admin_msg_id=None
        if ADMIN_CHAT_ID:
            u=update.effective_user
            m=ctx.bot.send_message(ADMIN_CHAT_ID, f"🆕 Нове замовлення {o}\n🕒 {ts}\n👤 Клієнт: {u.full_name} (id {u.id})\n\n{summ}\n\nСтатус: 🟡 Нове — {ts}", reply_markup=kb_admin(o))
            admin_msg_id=m.message_id
        m2=ctx.bot.send_message(update.effective_chat.id, f"{summ}\n\nСтатус: 🟡 Нове — {ts}", reply_markup=kb_user(o))
        ORD(ctx)[o]={"user_chat_id":update.effective_chat.id, "user_status_msg_id":m2.message_id, "admin_msg_id":admin_msg_id or 0, "summary":summ}

def on_admin_status(update:Update, ctx:CallbackContext):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o,action=update.callback_query.data.split(":",2)
    mp={"accept":"🟢 Прийнято","cooking":"👨‍🍳 Готуємо","courier":"🚴 Курʼєр в дорозі","done":"✅ Готово"}
    st=mp.get(action,"🟡 Нове"); ts=now_str()
    base=update.callback_query.message.text.split("\n\nСтатус:",1)[0]
    chat_on = ADMIN_FOR(ctx).get(ADMIN_CHAT_ID)==o or USER_FOR(ctx).get(ORD(ctx).get(o,{}).get("user_chat_id",0))==o
    update.callback_query.edit_message_text(base+f"\n\nСтатус: {st} — {ts}", reply_markup=kb_admin(o, chat_on=chat_on))
    reg=ORD(ctx).get(o)
    if reg:
        try:
            ctx.bot.edit_message_text(chat_id=reg["user_chat_id"], message_id=reg["user_status_msg_id"], text=f"{reg['summary']}\n\nСтатус: {st} — {ts}", reply_markup=kb_user(o, chat_on=chat_on))
        except Exception as e:
            log.warning("edit user msg fail: %s", e)
        try:
            ctx.bot.send_message(reg["user_chat_id"], f"Статус вашого замовлення змінено на: {st} — {ts}")
            if action=="done" and DONE_STICKER_FILE_ID:
                ctx.bot.send_sticker(reg["user_chat_id"], DONE_STICKER_FILE_ID)
        except Exception as e:
            log.warning("notify fail: %s", e)

def on_adminmsg(update:Update, ctx:CallbackContext):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o=update.callback_query.data.split(":",1)
    ADMIN_FOR(ctx)[ADMIN_CHAT_ID]=o
    reg=ORD(ctx).get(o,{})
    if reg:
        USER_FOR(ctx)[reg.get("user_chat_id")]=o
        try:
            ctx.bot.send_message(reg["user_chat_id"], f"🔔 Адміністратор розпочав чат по {o}. Ви можете відповідати тут.")
        except Exception: pass
    update.callback_query.answer("Чат з клієнтом активовано. Напишіть повідомлення…")
    update.callback_query.edit_message_reply_markup(kb_admin(o, chat_on=True))

def on_usermsg(update:Update, ctx:CallbackContext):
    ack(update)
    _, o = update.callback_query.data.split(":",1)
    USER_FOR(ctx)[update.effective_chat.id]=o
    try:
        ctx.bot.send_message(ADMIN_CHAT_ID, f"🔔 Клієнт розпочав чат по {o}. Відповідайте цим діалогом.")
    except Exception: pass
    update.callback_query.edit_message_reply_markup(kb_user(o, chat_on=True))

def on_admin_chat_end(update:Update, ctx:CallbackContext):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o=update.callback_query.data.split(":",1)
    if ADMIN_FOR(ctx).get(ADMIN_CHAT_ID)==o:
        ADMIN_FOR(ctx).pop(ADMIN_CHAT_ID, None)
    reg = ORD(ctx).get(o,{})
    if reg and USER_FOR(ctx).get(reg.get("user_chat_id"))==o:
        USER_FOR(ctx).pop(reg.get("user_chat_id"), None)
        try:
            ctx.bot.send_message(reg["user_chat_id"], f"🛑 Чат з адміністратором завершено по {o}.")
        except Exception: pass
    update.callback_query.answer("Чат завершено.")
    update.callback_query.edit_message_reply_markup(kb_admin(o, chat_on=False))

def on_user_chat_end(update:Update, ctx:CallbackContext):
    ack(update)
    _,o=update.callback_query.data.split(":",1)
    reg = ORD(ctx).get(o,{})
    if reg and USER_FOR(ctx).get(update.effective_chat.id)==o:
        USER_FOR(ctx).pop(update.effective_chat.id, None)
    if ADMIN_FOR(ctx).get(ADMIN_CHAT_ID)==o:
        ADMIN_FOR(ctx).pop(ADMIN_CHAT_ID, None)
        try:
            ctx.bot.send_message(ADMIN_CHAT_ID, f"🛑 Клієнт завершив чат по {o}.")
        except Exception: pass
    update.callback_query.answer("Чат завершено.")
    update.callback_query.edit_message_reply_markup(kb_user(o, chat_on=False))

def on_text(update:Update, ctx:CallbackContext):
    ensure_globals(ctx)
    chat_id = update.effective_chat.id
    txt=(update.message.text or "").strip()

    # ----- режим двостороннього чату -----
    if update.effective_user and update.effective_user.id==ADMIN_CHAT_ID:
        o = ADMIN_FOR(ctx).get(ADMIN_CHAT_ID)
        if o:
            reg = ORD(ctx).get(o,{})
            user_chat_id = reg.get("user_chat_id")
            if user_chat_id:
                ctx.bot.send_message(user_chat_id, f"📩 Адмін: {txt}")
                return

    o = USER_FOR(ctx).get(chat_id)
    if o and ADMIN_CHAT_ID:
        ctx.bot.send_message(ADMIN_CHAT_ID, f"👤 Клієнт: {txt}")
        return
    # -------------------------------------

    s=S(ctx)

    if s.awaiting=="ship":
        if txt==SHIP_DELIVERY:
            s.delivery="delivery"; s.awaiting=None; return render_addr(update,ctx)
        if txt==SHIP_PICKUP:
            s.delivery="pickup"; s.awaiting=None; return render_phone(update,ctx)
        # якщо щось інше — просто повторно покажемо кнопки
        return send_delivery_screen(ctx, chat_id)

    if s.awaiting=="addr":
        s.address=txt; s.awaiting=None; return render_phone(update,ctx)

    if s.awaiting=="phone":
        if PHONE_RE.match(txt):
            s.phone=txt; s.awaiting=None; return render_home(update,ctx)
        masked = format_phone_mask(txt)
        if masked:
            s.phone=masked; s.awaiting=None; return render_home(update,ctx)
        return ctx.bot.send_message(
            update.effective_chat.id,
            "❗️ Невірний формат. Спробуйте ще раз у вигляді:\n<b>+38 (0XX)-XXX-XX-XX</b>",
            parse_mode=ParseMode.HTML
        )

    if s.awaiting=="comment":
        s.comment=txt; s.awaiting=None
        ctx.bot.send_message(update.effective_chat.id, "Коментар додано ✅", reply_markup=ReplyKeyboardRemove())
        push(s,"summary")
        return ctx.bot.send_message(update.effective_chat.id, summarize(s), reply_markup=kb_summary(), disable_web_page_preview=True)

    return ctx.bot.send_message(update.effective_chat.id, "Надішліть /start або користуйтесь кнопками.")

def main():
    up=Updater(TOKEN, use_context=True); dp=up.dispatcher
    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("reset", cmd_reset))
    dp.add_handler(CommandHandler("menu", cmd_menu))
    dp.add_handler(CommandHandler("ping", cmd_ping))
    dp.add_handler(CallbackQueryHandler(on_ship, pattern=r"^ship:"))
    dp.add_handler(CallbackQueryHandler(on_nav,  pattern=r"^nav:"))
    dp.add_handler(CallbackQueryHandler(on_sw,   pattern=r"^sw:"))
    dp.add_handler(CallbackQueryHandler(on_add_yesno, pattern=r"^add:(yes|no)$"))
    dp.add_handler(CallbackQueryHandler(on_add, pattern=r"^add:(toggle|continue|qty):"))
    dp.add_handler(CallbackQueryHandler(on_sd, pattern=r"^sd:"))
    dp.add_handler(CallbackQueryHandler(on_ds, pattern=r"^ds:"))
    dp.add_handler(CallbackQueryHandler(on_dr, pattern=r"^dr:"))
    dp.add_handler(CallbackQueryHandler(on_cart, pattern=r"^cart:open$"))
    dp.add_handler(CallbackQueryHandler(on_order, pattern=r"^order:confirm$"))
    dp.add_handler(CallbackQueryHandler(on_admin_status, pattern=r"^admin:"))
    dp.add_handler(CallbackQueryHandler(on_adminmsg, pattern=r"^adminmsg:"))
    dp.add_handler(CallbackQueryHandler(on_admin_chat_end, pattern=r"^adminchatend:"))
    dp.add_handler(CallbackQueryHandler(on_user_chat_end, pattern=r"^userchatend:"))
    dp.add_handler(CallbackQueryHandler(on_usermsg, pattern=r"^usermsg:"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, on_text))
    log.info("Starting polling (PTB 13.x)…")
    up.start_polling(drop_pending_updates=True)
    up.idle()

if __name__=="__main__":
    main()
