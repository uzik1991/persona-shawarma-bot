#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, logging, datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, Contact
)
from telegram.ext import (
    Updater, CallbackContext, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters
)

TOKEN = os.environ.get("TELEGRAM_TOKEN","").strip()
if not TOKEN:
    raise SystemExit("Set TELEGRAM_TOKEN env var")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID","0") or "0")
DONE_STICKER_FILE_ID = os.environ.get("DONE_STICKER_FILE_ID","").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("shawarma-bot13")

def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")

SHAWARMA = {"koko":{"name":"Коко","price":260},"disney":{"name":"Дісней","price":160}}
ADDONS   = {"onion":{"name":"Цибуля","price":10},"mozz":{"name":"Моцарелла","price":20}}
SIDES    = {"sweet_fries":{"name":"Батат-фрі","price":185,"note":"подається з трюфельним соусом"},
            "dips":{"name":"Діпи","price":150,"note":"подається з сирним соусом"},
            "falafel":{"name":"Фалафель","price":165,"note":"подається з хумусом"},
            "cheese_balls":{"name":"Сирні кульки","price":140,"note":"подається з ягідним соусом"}}
DESSERTS = {"pear_dorblu":{"name":"Торт Груша-Дорблю","price":160},
            "carrot":{"name":"Торт Морквʼяний","price":150},
            "brownie":{"name":"Брауні","price":130}}
DRINKS   = {"cola":{"name":"Кола","price":70},"ayran":{"name":"Айран","price":95},"capp":{"name":"Капучино","price":120}}

SEQ_PATH="order_seq.json"
def next_order_no() -> str:
    today = dt.datetime.now().strftime("%Y%m%d")
    seq = 0
    if os.path.exists(SEQ_PATH):
        try:
            d = json.load(open(SEQ_PATH,"r",encoding="utf-8"))
            if d.get("date")==today:
                seq = int(d.get("seq",0))
        except Exception:
            pass
    seq += 1
    json.dump({"date":today,"seq":seq}, open(SEQ_PATH,"w",encoding="utf-8"))
    return f"T{today}-{seq:04d}"

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

def S(ctx: CallbackContext) -> Session:
    if "S" not in ctx.user_data:
        ctx.user_data["S"]=Session()
    return ctx.user_data["S"]

def ensure_globals(ctx: CallbackContext):
    ctx.bot_data.setdefault("orders",{})
    ctx.bot_data.setdefault("await_admin_dm",{})
    ctx.bot_data.setdefault("await_user_dm",{})

def ORD(ctx): ensure_globals(ctx); return ctx.bot_data["orders"]
def set_admin_dm(ctx,admin,order_no): ensure_globals(ctx); ctx.bot_data["await_admin_dm"][admin]=order_no
def pop_admin_dm(ctx,admin): ensure_globals(ctx); return ctx.bot_data["await_admin_dm"].pop(admin,None)
def set_user_dm(ctx,user,order_no): ensure_globals(ctx); ctx.bot_data["await_user_dm"][user]=order_no
def pop_user_dm(ctx,user): ensure_globals(ctx); return ctx.bot_data["await_user_dm"].pop(user,None)

PHONE_SHARE_BTN="📱 Поділитись контактом"
PHONE_MANUAL_BTN="Ввести вручну"
def _digits(t:str)->str: return "".join(ch for ch in t if ch.isdigit())
def format_phone_mask(text:str)->Optional[str]:
    d=_digits(text)
    if len(d)>=12 and d.startswith("38"):
        core=d[2:12]
        if len(core)==10:
            return f"+38 ({core[0:3]}) - {core[3:6]} - {core[6:8]} - {core[8:10]}"
    return None
def kb_phone_choice()->ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(PHONE_SHARE_BTN, request_contact=True)],
         [KeyboardButton(PHONE_MANUAL_BTN)]],
        resize_keyboard=True, one_time_keyboard=True
    )

def kb_main()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌯 Шаурма",  callback_data="nav:shawarma")],
        [InlineKeyboardButton("🍟 Сайди",   callback_data="nav:sides")],
        [InlineKeyboardButton("🍰 Десерти", callback_data="nav:desserts")],
        [InlineKeyboardButton("🥤 Напої",   callback_data="nav:drinks")],
        [InlineKeyboardButton("🧺 Кошик",   callback_data="cart:open")],
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
def kb_qty(scope:str,item_id:str)->InlineKeyboardMarkup:
    rows=[[InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (1,2,3)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (4,5,6)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (7,8,9)],
          [InlineKeyboardButton("⬅️ Назад","nav:back")]]
    return InlineKeyboardMarkup(rows)
def kb_yesno(tag)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Так",f"{tag}:yes")],
                                 [InlineKeyboardButton("Ні",f"{tag}:no")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_comment()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустити","comment:skip")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_summary()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Підтвердити ✅","order:confirm")],
                                 [InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_admin(order_no:str)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Прийняти 🟢",f"admin:{order_no}:accept")],
        [InlineKeyboardButton("Готуємо 👨‍🍳",f"admin:{order_no}:cooking")],
        [InlineKeyboardButton("Курʼєр 🚴",f"admin:{order_no}:courier")],
        [InlineKeyboardButton("Готово ✅",f"admin:{order_no}:done")],
        [InlineKeyboardButton("✉️ Написати клієнту", f"adminmsg:{order_no}")]
    ])
def kb_user(order_no:str)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Нове замовлення","nav:restart")],
        [InlineKeyboardButton("✉️ Написати адміну", f"usermsg:{order_no}")]
    ])

def push(s:Session, tag:str):
    if not s.history or s.history[-1]!=tag:
        s.history.append(tag)

def render_delivery(update:Update, ctx:CallbackContext):
    s=S(ctx); s.history=[]; push(s,"delivery")
    if update.message:
        update.message.reply_text("Вітаю! Оберіть спосіб отримання:", reply_markup=None)
    update.effective_chat.send_message(
        "Обери: доставка або самовивіз.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚚 Доставка","ship:delivery")],
            [InlineKeyboardButton("🏃‍♀️ Самовивіз","ship:pickup")]
        ])
    )
def render_addr(update:Update, ctx:CallbackContext):
    s=S(ctx); s.awaiting="addr"; push(s,"addr")
    update.effective_chat.send_message("Введіть адресу доставки:", reply_markup=ReplyKeyboardRemove())
    update.effective_chat.send_message(" ", reply_markup=kb_back())
def render_phone(update:Update, ctx:CallbackContext, replace=True):
    s=S(ctx); s.awaiting="phone"; push(s,"phone_wait")
    f = (update.callback_query.edit_message_text
         if replace and update.callback_query else
         update.effective_chat.send_message)
    f("Поділитись контактом або ввести номер вручну?", reply_markup=kb_back())
    update.effective_chat.send_message(
        "Натисни «Поділитись контактом» або введи у форматі:\n"
        "<b>+38 (xxx) - xxx - xx - xx</b>\nНапр.: +38 (067) - 123 - 45 - 67",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_phone_choice()
    )
def render_home(update:Update, ctx:CallbackContext, replace=True):
    s=S(ctx); push(s,"home")
    update.effective_chat.send_message("Що бажаєте сьогодні?", reply_markup=kb_main())
def render_select(update:Update, ctx:CallbackContext, scope:str, selected:Set[str], data:Dict):
    push(S(ctx), f"{scope}_select")
    update.effective_chat.send_message("Оберіть позиції (можна кілька):", reply_markup=kb_check(data, selected, scope))
def render_qty(update:Update, ctx:CallbackContext, scope:str, seq:List[str], idx:int):
    push(S(ctx), f"{scope}_qty:{idx}")
    item_id = seq[idx]
    data = {"sw":SHAWARMA,"add":ADDONS,"sd":SIDES,"ds":DESSERTS,"dr":DRINKS}[scope]
    item = data[item_id]
    update.effective_chat.send_message(f"Скільки «{item['name']}»?", reply_markup=kb_qty(scope,item_id))
def render_comment(update:Update, ctx:CallbackContext):
    s=S(ctx); s.awaiting="comment"; push(s,"comment")
    update.effective_chat.send_message("Додати коментар? Надішліть текст або «Пропустити».", reply_markup=ReplyKeyboardRemove())
    update.effective_chat.send_message(" ", reply_markup=kb_comment())
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
    update.effective_chat.send_message(summarize(s), reply_markup=kb_summary(), disable_web_page_preview=True)

def cmd_start(update:Update, ctx:CallbackContext):
    ctx.user_data["S"]=Session()
    u=update.effective_user
    update.message.reply_text(f"Вітаю, {u.full_name}! 😊")
    render_delivery(update,ctx)
def cmd_help(update:Update, ctx:CallbackContext):
    update.message.reply_text("Команди: /start")

def on_contact(update:Update, ctx:CallbackContext):
    s=S(ctx); c:Contact=update.message.contact
    masked = format_phone_mask(c.phone_number or "")
    if not masked:
        return update.message.reply_text("Не вдалося прочитати номер. Введіть вручну за маскою.")
    s.phone = masked; s.awaiting=None
    update.message.reply_text(f"Телефон збережено: {masked}")
    render_home(update,ctx,False)

def on_text(update:Update, ctx:CallbackContext):
    ensure_globals(ctx)
    if update.effective_user and update.effective_user.id==ADMIN_CHAT_ID:
        order_no = pop_admin_dm(ctx, ADMIN_CHAT_ID)
        if order_no:
            reg = ORD(ctx).get(order_no)
            if reg and reg.get("user_chat_id"):
                ctx.bot.send_message(reg["user_chat_id"],
                    f"📩 Повідомлення від адміністратора по {order_no}:\n\n{update.message.text}")
                update.message.reply_text("Надіслано клієнту ✅")
                return
    ou = update.effective_chat.id
    order_no = pop_user_dm(ctx, ou)
    if order_no and ADMIN_CHAT_ID:
        u = update.effective_user
        ctx.bot.send_message(ADMIN_CHAT_ID,
            f"📨 Повідомлення від клієнта по {order_no}\n"
            f"👤 {u.full_name} (id {u.id})\n\n{update.message.text}")
        update.message.reply_text("Надіслано адміну ✅")
        return
    s=S(ctx); txt=(update.message.text or "").strip()
    if txt==PHONE_MANUAL_BTN and s.awaiting=="phone":
        update.message.reply_text(
            "Введіть номер у форматі: <b>+38 (xxx) - xxx - xx - xx</b>",
            parse_mode=ParseMode.HTML
        ); return
    if s.awaiting=="addr":
        s.address=txt; s.awaiting=None; render_phone(update,ctx,False); return
    if s.awaiting=="phone":
        masked = format_phone_mask(txt)
        if not masked:
            update.message.reply_text(
                "❗️ Невірний формат. Спробуйте ще раз у вигляді:\n"
                "<b>+38 (xxx) - xxx - xx - xx</b>", parse_mode=ParseMode.HTML
            ); return
        s.phone=masked; s.awaiting=None; render_home(update,ctx,False); return
    if s.awaiting=="comment":
        s.comment=txt; s.awaiting=None
        update.message.reply_text("Коментар додано ✅")
        update.effective_chat.send_message(summarize(s), reply_markup=kb_summary(), disable_web_page_preview=True)
        push(s,"summary"); return
    update.message.reply_text("Надішліть /start для меню або користуйтесь кнопками.")

def ack(update:Update):
    try: update.callback_query.answer()
    except Exception: pass

def on_ship(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx)
    if update.callback_query.data=="ship:delivery":
        s.delivery="delivery"; render_addr(update,ctx)
    else:
        s.delivery="pickup"; render_phone(update,ctx)

def on_nav(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx); data=update.callback_query.data.split(":",1)[1]
    if data=="back":
        if not s.history: return render_delivery(update,ctx)
        s.history.pop()
        if not s.history: return render_delivery(update,ctx)
        tag=s.history[-1]
        if tag=="delivery": return render_delivery(update,ctx)
        if tag=="addr": return render_addr(update,ctx)
        if tag=="phone_wait": return render_phone(update,ctx)
        if tag=="home": return render_home(update,ctx)
        if tag.endswith("_select"):
            scope=tag.split("_")[0]
            mapping={"sw":SHAWARMA,"sd":SIDES,"ds":DESSERTS,"dr":DRINKS,"add":ADDONS}
            sel={"sw":s.sel_sw,"sd":s.sel_sd,"ds":s.sel_ds,"dr":s.sel_dr,"add":s.sel_add}[scope]
            return render_select(update,ctx,scope,sel,mapping[scope])
        if tag.startswith("sw_qty:"):
            s.i_sw=int(tag.split(":")[1]); return render_qty(update,ctx,"sw", s.q_sw, s.i_sw)
        if tag.startswith("sd_qty:"):
            s.i_sd=int(tag.split(":")[1]); return render_qty(update,ctx,"sd", s.q_sd, s.i_sd)
        if tag.startswith("ds_qty:"):
            s.i_ds=int(tag.split(":")[1]); return render_qty(update,ctx,"ds", s.q_ds, s.i_ds)
        if tag.startswith("dr_qty:"):
            s.i_dr=int(tag.split(":")[1]); return render_qty(update,ctx,"dr", s.q_dr, s.i_dr)
        if tag=="comment": return render_comment(update,ctx)
        if tag=="summary": return render_summary(update,ctx)
        return render_home(update,ctx)
    if data=="restart":
        ctx.user_data["S"]=Session(); return render_delivery(update,ctx)
    if data=="shawarma":
        s.sel_sw=set(); s.q_sw=[]; s.i_sw=0; return render_select(update,ctx,"sw", s.sel_sw, SHAWARMA)
    if data=="sides":
        s.sel_sd=set(); s.q_sd=[]; s.i_sd=0; return render_select(update,ctx,"sd", s.sel_sd, SIDES)
    if data=="desserts":
        s.sel_ds=set(); s.q_ds=[]; s.i_ds=0; return render_select(update,ctx,"ds", s.sel_ds, DESSERTS)
    if data=="drinks":
        s.sel_dr=set(); s.q_dr=[]; s.i_dr=0; return render_select(update,ctx,"dr", s.sel_dr, DRINKS)

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
        return update.effective_chat.send_message("Чи потрібно щось додати в шаурму?", reply_markup=kb_yesno("add"))
    return on_group(update,ctx,"sw", S(ctx).sel_sw, S(ctx).q_sw, "i_sw", S(ctx).b_sw, SHAWARMA, after)
def on_add_yesno(update:Update, ctx:CallbackContext):
    ack(update); s=S(ctx); _,ans=update.callback_query.data.split(":")
    push(s,"add_yesno")
    if ans=="yes":
        s.sel_add=set(); s.q_add=[]; s.i_add=0
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
        m2=update.effective_chat.send_message(f"{summ}\n\nСтатус: 🟡 Нове — {ts}", reply_markup=kb_user(o))
        ORD(ctx)[o]={"user_chat_id":update.effective_chat.id, "user_status_msg_id":m2.message_id, "admin_msg_id":admin_msg_id or 0, "summary":summ}
def on_admin_status(update:Update, ctx:CallbackContext):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID:
        return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o,action=update.callback_query.data.split(":",2)
    mp={"accept":"🟢 Прийнято","cooking":"👨‍🍳 Готуємо","courier":"🚴 Курʼєр в дорозі","done":"✅ Готово"}
    st=mp.get(action,"🟡 Нове"); ts=now_str()
    base=update.callback_query.message.text.split("\n\nСтатус:",1)[0]
    update.callback_query.edit_message_text(base+f"\n\nСтатус: {st} — {ts}", reply_markup=kb_admin(o))
    reg=ORD(ctx).get(o)
    if reg:
        try:
            ctx.bot.edit_message_text(chat_id=reg["user_chat_id"], message_id=reg["user_status_msg_id"], text=f"{reg['summary']}\n\nСтатус: {st} — {ts}", reply_markup=kb_user(o))
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
    _,o=update.callback_query.data.split(":",1); set_admin_dm(ctx, ADMIN_CHAT_ID, o)
    update.callback_query.answer("Напишіть текст повідомлення клієнту…")
    update.callback_query.edit_message_reply_markup(kb_admin(o))
def on_usermsg(update:Update, ctx:CallbackContext):
    ack(update); _,o=update.callback_query.data.split(":",1); set_user_dm(ctx, update.effective_chat.id, o)
    update.callback_query.answer("Напишіть текст адміну…")
    update.callback_query.edit_message_reply_markup(kb_user(o))

def main():
    up=Updater(TOKEN, use_context=True); dp=up.dispatcher
    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CallbackQueryHandler(on_ship, pattern=r"^ship:"))
    dp.add_handler(CallbackQueryHandler(on_nav, pattern=r"^nav:"))
    dp.add_handler(CallbackQueryHandler(on_sw,  pattern=r"^sw:"))
    dp.add_handler(CallbackQueryHandler(on_add_yesno, pattern=r"^add:(yes|no)$"))
    dp.add_handler(CallbackQueryHandler(on_add, pattern=r"^add:(toggle|continue|qty):"))
    dp.add_handler(CallbackQueryHandler(on_sd, pattern=r"^sd:"))
    dp.add_handler(CallbackQueryHandler(on_ds, pattern=r"^ds:"))
    dp.add_handler(CallbackQueryHandler(on_dr, pattern=r"^dr:"))
    dp.add_handler(CallbackQueryHandler(on_cart, pattern=r"^cart:open$"))
    dp.add_handler(CallbackQueryHandler(on_order, pattern=r"^order:confirm$"))
    dp.add_handler(CallbackQueryHandler(on_admin_status, pattern=r"^admin:"))
    dp.add_handler(CallbackQueryHandler(on_adminmsg, pattern=r"^adminmsg:"))
    dp.add_handler(CallbackQueryHandler(on_usermsg, pattern=r"^usermsg:"))
    dp.add_handler(MessageHandler(Filters.contact, on_contact))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, on_text))
    log.info("Starting polling (PTB 13.x)…")
    up.start_polling(drop_pending_updates=True)
    up.idle()

if __name__=="__main__":
    main()
