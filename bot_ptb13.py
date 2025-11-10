#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, logging, datetime as dt, re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, Contact
from telegram.ext import Updater, CallbackContext, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
TOKEN = os.environ.get("TELEGRAM_TOKEN","").strip()
if not TOKEN: raise SystemExit("Set TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID","0") or "0")
DONE_STICKER_FILE_ID = os.environ.get("DONE_STICKER_FILE_ID","").strip()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("shawarma-bot")
def now_str(): return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
SHAWARMA = {"koko":{"name":"Коко","price":260},"disney":{"name":"Дісней","price":160}}
ADDONS   = {"onion":{"name":"Цибуля","price":10},"mozz":{"name":"Моцарелла","price":20}}
SIDES    = {"sweet_fries":{"name":"Батат-фрі","price":185,"note":"подається з трюфельним соусом"},"dips":{"name":"Діпи","price":150,"note":"подається з сирним соусом"},"falafel":{"name":"Фалафель","price":165,"note":"подається з хумусом"},"cheese_balls":{"name":"Сирні кульки","price":140,"note":"подається з ягідним соусом"}}
DESSERTS = {"pear_dorblu":{"name":"Торт Груша-Дорблю","price":160},"carrot":{"name":"Торт Морквʼяний","price":150},"brownie":{"name":"Брауні","price":130}}
DRINKS   = {"cola":{"name":"Кола","price":70},"ayran":{"name":"Айран","price":95},"capp":{"name":"Капучино","price":120}}
SEQ_PATH="order_seq.json"
def next_order_no():
    today=dt.datetime.now().strftime("%Y%m%d"); seq=0
    if os.path.exists(SEQ_PATH):
        try:
            d=json.load(open(SEQ_PATH,"r",encoding="utf-8"))
            if d.get("date")==today: seq=int(d.get("seq",0))
        except Exception: pass
    seq+=1; json.dump({"date":today,"seq":seq}, open(SEQ_PATH,"w",encoding="utf-8"))
    return f"T{today}-{seq:04d}"
@dataclass
class Session:
    history: List[str]=field(default_factory=list)
    delivery: Optional[str]=None
    address: Optional[str]=None
    phone: Optional[str]=None
    comment: str=""
    sel_sw:Set[str]=field(default_factory=set); sel_add:Set[str]=field(default_factory=set)
    sel_sd:Set[str]=field(default_factory=set); sel_ds:Set[str]=field(default_factory=set); sel_dr:Set[str]=field(default_factory=set)
    q_sw:List[str]=field(default_factory=list); i_sw:int=0
    q_add:List[str]=field(default_factory=list); i_add:int=0
    b_sw:Dict[str,int]=field(default_factory=dict); b_add:Dict[str,int]=field(default_factory=dict)
    awaiting: Optional[str]=None
    order_no: Optional[str]=None
def S(ctx): 
    if "S" not in ctx.user_data: ctx.user_data["S"]=Session()
    return ctx.user_data["S"]
def ensure_globals(ctx):
    ctx.bot_data.setdefault("orders",{}); ctx.bot_data.setdefault("await_admin_dm",{}); ctx.bot_data.setdefault("await_user_dm",{})
def ORD(ctx): ensure_globals(ctx); return ctx.bot_data["orders"]
def set_admin_dm(ctx,admin,order_no): ensure_globals(ctx); ctx.bot_data["await_admin_dm"][admin]=order_no
def pop_admin_dm(ctx,admin): ensure_globals(ctx); return ctx.bot_data["await_admin_dm"].pop(admin,None)
def set_user_dm(ctx,user,order_no): ensure_globals(ctx); ctx.bot_data["await_user_dm"][user]=order_no
def pop_user_dm(ctx,user): ensure_globals(ctx); return ctx.bot_data["await_user_dm"].pop(user,None)
PHONE_SHARE_BTN="📱 Поділитись контактом"; PHONE_MANUAL_BTN="Ввести вручну"
def _digits(t): return "".join(ch for ch in t if ch.isdigit())
def format_phone_mask(text):
    d=_digits(text)
    if len(d)>=12 and d.startswith("38"):
        core=d[2:12]
        if len(core)==10: return f"+38 ({core[0:3]}) - {core[3:6]} - {core[6:8]} - {core[8:10]}"
    return None
LBL={"shawarma":"🌯 Шаурма","sides":"🍟 Сайди","desserts":"🍰 Десерти","drinks":"🥤 Напої","cart":"🧺 Кошик","new":"🆕 Нове замовлення","admin":"✉️ Написати адміну"}
def kb_persistent(): 
    return ReplyKeyboardMarkup([[KeyboardButton(LBL["shawarma"]),KeyboardButton(LBL["sides"])],[KeyboardButton(LBL["desserts"]),KeyboardButton(LBL["drinks"])],[KeyboardButton(LBL["cart"]),KeyboardButton(LBL["new"])],[KeyboardButton(LBL["admin"])]], resize_keyboard=True, one_time_keyboard=False)
def kb_request_phone():
    return ReplyKeyboardMarkup([[KeyboardButton(PHONE_SHARE_BTN, request_contact=True)],[KeyboardButton(PHONE_MANUAL_BTN)]], resize_keyboard=True, one_time_keyboard=True)
def kb_back(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_check(options, selected, scope, cont=True):
    rows=[]
    for oid,meta in options.items():
        mark="☑" if oid in selected else "□"
        rows.append([InlineKeyboardButton(f"{mark} {meta['name']} — {meta['price']} грн", f"{scope}:toggle:{oid}")])
    if cont: rows.append([InlineKeyboardButton("Продовжити ▶️", f"{scope}:continue")])
    rows.append([InlineKeyboardButton("⬅️ Назад","nav:back")])
    return InlineKeyboardMarkup(rows)
def kb_qty(scope,item_id):
    rows=[[InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (1,2,3)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (4,5,6)],
          [InlineKeyboardButton(str(n), f"{scope}:qty:{item_id}:{n}") for n in (7,8,9)],
          [InlineKeyboardButton("⬅️ Назад","nav:back")]]
    return InlineKeyboardMarkup(rows)
def kb_yesno(tag): return InlineKeyboardMarkup([[InlineKeyboardButton("Так",f"{tag}:yes")],[InlineKeyboardButton("Ні",f"{tag}:no")],[InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_comment(): return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустити","comment:skip")],[InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_summary(): return InlineKeyboardMarkup([[InlineKeyboardButton("Підтвердити ✅","order:confirm")],[InlineKeyboardButton("⬅️ Назад","nav:back")]])
def kb_admin(order_no): 
    return InlineKeyboardMarkup([[InlineKeyboardButton("Прийняти 🟢",f"admin:{order_no}:accept")],[InlineKeyboardButton("Готуємо 👨‍🍳",f"admin:{order_no}:cooking")],[InlineKeyboardButton("Курʼєр 🚴",f"admin:{order_no}:courier")],[InlineKeyboardButton("Готово ✅",f"admin:{order_no}:done")],[InlineKeyboardButton("✉️ Написати клієнту", f"adminmsg:{order_no}")]])
def kb_user(order_no):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🆕 Нове замовлення","nav:restart")],[InlineKeyboardButton("✉️ Написати адміну", f"usermsg:{order_no}")]])
def push(Ses, tag):
    if not Ses.history or Ses.history[-1]!=tag: Ses.history.append(tag)
def render_delivery(update,ctx):
    Ses=S(ctx); Ses.history=[]; push(Ses,"delivery")
    if update.message: update.message.reply_text("Меню знизу 👇", reply_markup=kb_persistent())
    update.effective_chat.send_message(f"Вітаю, {update.effective_user.first_name}!\\nОбери: доставка або самовивіз.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚚 Доставка","ship:delivery")],[InlineKeyboardButton("🏃‍♀️ Самовивіз","ship:pickup")]]))
def render_addr(update,ctx):
    Ses=S(ctx); Ses.awaiting="addr"; push(Ses,"addr")
    update.effective_chat.send_message("Введіть адресу доставки:", reply_markup=ReplyKeyboardRemove())
    update.effective_chat.send_message(" ", reply_markup=kb_back())
def render_phone_choice(update,ctx):
    Ses=S(ctx); Ses.awaiting=None; push(Ses,"phone_choice")
    update.effective_chat.send_message("Поділитись контактом або ввести номер вручну?", reply_markup=kb_request_phone())
    update.effective_chat.send_message(" ", reply_markup=kb_back())
def render_phone_manual(update,ctx):
    Ses=S(ctx); Ses.awaiting="phone"; push(Ses,"phone")
    update.effective_chat.send_message("Введіть номер у форматі: <b>+38 (xxx) - xxx - xx - xx</b>\\nНапр.: +38 (067) - 123 - 45 - 67", parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
    update.effective_chat.send_message(" ", reply_markup=kb_back())
def render_home(update,ctx):
    Ses=S(ctx); push(Ses,"home")
    update.effective_chat.send_message("Що бажаєте сьогодні?", reply_markup=kb_persistent())
def render_sw_select(update,ctx):
    Ses=S(ctx); push(Ses,"sw_select")
    update.effective_chat.send_message("Оберіть шаурму (можна кілька):", reply_markup=kb_check(SHAWARMA,Ses.sel_sw,"sw"))
def render_sw_qty(update,ctx):
    Ses=S(ctx); push(Ses,f"sw_qty:{Ses.i_sw}"); iid=Ses.q_sw[Ses.i_sw]; item=SHAWARMA[iid]
    update.effective_chat.send_message(f"Скільки «{item['name']}»?", reply_markup=kb_qty("sw",iid))
def render_add_yesno(update,ctx):
    Ses=S(ctx); push(Ses,"add_yesno")
    update.effective_chat.send_message("Чи потрібно щось додати в шаурму?", reply_markup=kb_yesno("add"))
def render_add_select(update,ctx):
    Ses=S(ctx); push(Ses,"add_select")
    update.effective_chat.send_message("Оберіть додатки (можна кілька):", reply_markup=kb_check(ADDONS,Ses.sel_add,"add"))
def render_add_qty(update,ctx):
    Ses=S(ctx); push(Ses,f"add_qty:{Ses.i_add}"); aid=Ses.q_add[Ses.i_add]; addon=ADDONS[aid]
    update.effective_chat.send_message(f"Скільки порцій «{addon['name']}»?", reply_markup=kb_qty("add",aid))
def render_comment(update,ctx):
    Ses=S(ctx); Ses.awaiting="comment"; push(Ses,"comment")
    update.effective_chat.send_message("Додати коментар? Надішліть текст або «Пропустити».", reply_markup=ReplyKeyboardRemove())
    update.effective_chat.send_message(" ", reply_markup=kb_comment())
def summarize(Ses):
    total=0; lines=["Замовлення:"]
    for k,v in Ses.b_sw.items(): lines.append(f"Шаурма {SHAWARMA[k]['name']} — {v} шт"); total+=SHAWARMA[k]['price']*v
    if Ses.b_add:
        lines.append(""); lines.append("Додатки:")
        for k,v in Ses.b_add.items(): lines.append(f"{ADDONS[k]['name']} — {v} пор."); total+=ADDONS[k]['price']*v
    if Ses.delivery: lines.append(""); lines.append(f"Отримання: {'Доставка' if Ses.delivery=='delivery' else 'Самовивіз'}")
    if Ses.delivery=='delivery' and Ses.address: lines.append(f"Адреса: {Ses.address}")
    if Ses.phone: lines.append(f"Телефон: {Ses.phone}")
    if Ses.comment: lines.append(f"Коментар: {Ses.comment}")
    lines.append(""); lines.append(f"Ціна: {total} грн")
    Ses.order_no = Ses.order_no or next_order_no()
    return "Номер замовлення: "+Ses.order_no+"\\n\\n" + "\\n".join(lines)
def render_summary(update,ctx):
    Ses=S(ctx); push(Ses,"summary")
    update.effective_chat.send_message(summarize(Ses), reply_markup=kb_summary(), disable_web_page_preview=True)
def cmd_start(update,ctx):
    ctx.user_data["S"]=Session(); 
    if update.message: update.message.reply_text("Меню знизу 👇", reply_markup=kb_persistent())
    render_delivery(update,ctx)
def cmd_help(update,ctx): update.message.reply_text("Допомога: /start")
def on_contact(update,ctx):
    Ses=S(ctx); c:Contact=update.message.contact; masked=format_phone_mask(c.phone_number or "")
    if not masked: return update.message.reply_text("Не вдалося прочитати номер. Введіть вручну.")
    Ses.phone=masked; Ses.awaiting=None; update.message.reply_text(f"Телефон збережено: {masked}"); render_home(update,ctx)
def on_text(update,ctx):
    ensure_globals(ctx); Ses=S(ctx); t=(update.message.text or "").strip()
    if update.effective_user.id==ADMIN_CHAT_ID:
        o=pop_admin_dm(ctx,ADMIN_CHAT_ID)
        if o:
            reg=ORD(ctx).get(o)
            if reg and reg.get("user_chat_id"): 
                ctx.bot.send_message(reg["user_chat_id"], f"📩 Повідомлення від адміністратора по {o}:\\n\\n{t}")
                return update.message.reply_text("Надіслано клієнту ✅")
    o=pop_user_dm(ctx, update.effective_chat.id)
    if o and ADMIN_CHAT_ID:
        u=update.effective_user
        ctx.bot.send_message(ADMIN_CHAT_ID, f"📨 Повідомлення від клієнта по {o}\\n👤 {u.full_name} (id {u.id})\\n\\n{t}")
        return update.message.reply_text("Надіслано адміну ✅")
    if t==PHONE_MANUAL_BTN: return render_phone_manual(update,ctx)
    if Ses.awaiting=="addr": Ses.address=t; Ses.awaiting=None; return render_phone_choice(update,ctx)
    if Ses.awaiting=="phone":
        m=format_phone_mask(t)
        if not m: return update.message.reply_text("❗️ Невірний формат. Приклад: +38 (067) - 123 - 45 - 67")
        Ses.phone=m; Ses.awaiting=None; return render_home(update,ctx)
    if Ses.awaiting=="comment": Ses.comment=t; Ses.awaiting=None; return render_summary(update,ctx)
    if t==LBL["shawarma"]: Ses.sel_sw=set(); Ses.q_sw=[]; Ses.i_sw=0; return render_sw_select(update,ctx)
    if t==LBL["sides"]:    return update.effective_chat.send_message("Скоро 😉")
    if t==LBL["desserts"]: return update.effective_chat.send_message("Скоро 😉")
    if t==LBL["drinks"]:   return update.effective_chat.send_message("Скоро 😉")
    if t==LBL["cart"]:     return update.effective_chat.send_message("<b>Кошик</b>", parse_mode=ParseMode.HTML)
    if t==LBL["new"]:      ctx.user_data["S"]=Session(); update.message.reply_text("Починаємо нове замовлення.", reply_markup=kb_persistent()); return render_delivery(update,ctx)
    if t==LBL["admin"]:    set_user_dm(ctx, update.effective_chat.id, Ses.order_no or "—"); return update.message.reply_text("Напишіть повідомлення адміну…")
    return update.message.reply_text("Скористайтесь нижнім меню або /start.")
def ack(update):
    try: update.callback_query.answer()
    except Exception: pass
def on_ship(update,ctx):
    ack(update); Ses=S(ctx)
    if update.callback_query.data=="ship:delivery": Ses.delivery="delivery"; render_addr(update,ctx)
    else: Ses.delivery="pickup"; render_phone_choice(update,ctx)
def on_nav(update,ctx):
    ack(update); Ses=S(ctx); data=update.callback_query.data.split(":",1)[1]
    if data=="restart": ctx.user_data["S"]=Session(); return render_delivery(update,ctx)
    if data=="back":
        if not Ses.history: return render_delivery(update,ctx)
        Ses.history.pop()
        if not Ses.history: return render_delivery(update,ctx)
        tag=Ses.history[-1]
        if tag.startswith("sw_qty:"): Ses.i_sw=int(tag.split(":")[1]); return render_sw_qty(update,ctx)
        if tag.startswith("add_qty:"): Ses.i_add=int(tag.split(":")[1]); return render_add_qty(update,ctx)
        return render_delivery(update,ctx) if tag=="delivery" else render_sw_select(update,ctx)
def on_sw(update,ctx):
    ack(update); Ses=S(ctx); _,action,*rest=update.callback_query.data.split(":")
    if action=="toggle":
        oid=rest[0]
        if oid in Ses.sel_sw: Ses.sel_sw.remove(oid)
        else: Ses.sel_sw.add(oid)
        return update.callback_query.edit_message_reply_markup(kb_check(SHAWARMA,Ses.sel_sw,"sw"))
    if action=="continue":
        if not Ses.sel_sw: return update.callback_query.answer("Виберіть хоча б одну позицію.", show_alert=True)
        Ses.q_sw=list(Ses.sel_sw); Ses.i_sw=0; return render_sw_qty(update,ctx)
    if action=="qty":
        iid,qty=rest[0], int(rest[1]); Ses.b_sw[iid]=Ses.b_sw.get(iid,0)+qty
        if Ses.i_sw+1 < len(Ses.q_sw): Ses.i_sw+=1; return render_sw_qty(update,ctx)
        return render_add_yesno(update,ctx)
def on_add(update,ctx):
    ack(update); Ses=S(ctx); _,action,*rest=update.callback_query.data.split(":")
    if action=="yes": Ses.sel_add=set(); Ses.q_add=[]; Ses.i_add=0; return render_add_select(update,ctx)
    if action=="no":  return render_comment(update,ctx)
    if action=="toggle":
        aid=rest[0]
        if aid in Ses.sel_add: Ses.sel_add.remove(aid)
        else: Ses.sel_add.add(aid)
        return update.callback_query.edit_message_reply_markup(kb_check(ADDONS,Ses.sel_add,"add"))
    if action=="continue":
        if not Ses.sel_add: return render_comment(update,ctx)
        Ses.q_add=list(Ses.sel_add); Ses.i_add=0; return render_add_qty(update,ctx)
    if action=="qty":
        aid,qty=rest[0], int(rest[1]); Ses.b_add[aid]=Ses.b_add.get(aid,0)+qty
        if Ses.i_add+1 < len(Ses.q_add): Ses.i_add+=1; return render_add_qty(update,ctx)
        return render_comment(update,ctx)
def finalize(update,ctx):
    Ses=S(ctx); o=Ses.order_no or next_order_no(); Ses.order_no=o; ts=now_str(); summ=summarize(Ses)
    admin_msg_id=None
    if ADMIN_CHAT_ID:
        u=update.effective_user
        m=ctx.bot.send_message(ADMIN_CHAT_ID, f"🆕 Нове замовлення {o}\\n🕒 {ts}\\n👤 Клієнт: {u.full_name} (id {u.id})\\n\\n{summ}\\n\\nСтатус: 🟡 Нове — {ts}", reply_markup=kb_admin(o))
        admin_msg_id=m.message_id
    m2=update.effective_chat.send_message(f"{summ}\\n\\nСтатус: 🟡 Нове — {ts}", reply_markup=kb_user(o))
    ORD(ctx)[o]={"user_chat_id":update.effective_chat.id, "user_status_msg_id":m2.message_id, "admin_msg_id":admin_msg_id or 0, "summary":summ}
def on_order(update,ctx):
    ack(update); 
    if update.callback_query.data=="order:confirm": return finalize(update,ctx)
def on_admin_status(update,ctx):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID: return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o,action=update.callback_query.data.split(":",2)
    mp={"accept":"🟢 Прийнято","cooking":"👨‍🍳 Готуємо","courier":"🚴 Курʼєр в дорозі","done":"✅ Готово"}; st=mp.get(action,"🟡 Нове"); ts=now_str()
    base=update.callback_query.message.text.split("\\n\\nСтатус:",1)[0]
    update.callback_query.edit_message_text(base+f"\\n\\nСтатус: {st} — {ts}", reply_markup=kb_admin(o))
    reg=ORD(ctx).get(o)
    if reg:
        try:
            ctx.bot.edit_message_text(chat_id=reg["user_chat_id"], message_id=reg["user_status_msg_id"], text=f"{reg['summary']}\\n\\nСтатус: {st} — {ts}", reply_markup=kb_user(o))
        except Exception as e: log.warning("edit user msg fail: %s", e)
        try:
            ctx.bot.send_message(reg["user_chat_id"], f"Статус вашого замовлення змінено на: {st} — {ts}")
            if action=="done" and DONE_STICKER_FILE_ID: ctx.bot.send_sticker(reg["user_chat_id"], DONE_STICKER_FILE_ID)
        except Exception as e: log.warning("notify fail: %s", e)
def on_adminmsg(update,ctx):
    ack(update)
    if update.effective_user.id!=ADMIN_CHAT_ID: return update.callback_query.answer("Недостатньо прав", show_alert=True)
    _,o=update.callback_query.data.split(":",1); set_admin_dm(ctx, ADMIN_CHAT_ID, o)
    update.callback_query.answer("Напишіть текст повідомлення клієнту…")
    update.callback_query.edit_message_reply_markup(kb_admin(o))
def on_usermsg(update,ctx):
    ack(update); _,o=update.callback_query.data.split(":",1); set_user_dm(ctx, update.effective_chat.id, o)
    update.callback_query.answer("Напишіть текст адміну…")
    update.callback_query.edit_message_reply_markup(kb_user(o))
def main():
    up=Updater(TOKEN, use_context=True); dp=up.dispatcher
    dp.add_handler(CommandHandler("start", cmd_start)); dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CallbackQueryHandler(on_ship, pattern=r"^ship:")); dp.add_handler(CallbackQueryHandler(on_nav, pattern=r"^nav:"))
    dp.add_handler(CallbackQueryHandler(on_sw, pattern=r"^sw:"));   dp.add_handler(CallbackQueryHandler(on_add, pattern=r"^add:"))
    dp.add_handler(CallbackQueryHandler(on_order, pattern=r"^order:confirm$"))
    dp.add_handler(CallbackQueryHandler(on_admin_status, pattern=r"^admin:"))
    dp.add_handler(CallbackQueryHandler(on_adminmsg, pattern=r"^adminmsg:"))
    dp.add_handler(CallbackQueryHandler(on_usermsg, pattern=r"^usermsg:"))
    dp.add_handler(MessageHandler(Filters.contact, on_contact)); dp.add_handler(MessageHandler(Filters.text & ~Filters.command, on_text))
    log.info("Starting polling…"); up.start_polling(drop_pending_updates=True); up.idle()
if __name__=="__main__": main()
