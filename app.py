import os
import sqlite3
import re
from datetime import datetime
from flask import Flask, request
import requests

# ============ SOZLAMALAR ============
BOT_TOKEN = "8926932530:AAEL9u6BD0V3cmbb8cfC-LjFm6PfQfg3u_8"  # @BotFather dan olgan tokening
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = os.path.join(os.path.dirname(__file__), "budget.db")

GEMINI_API_KEY = "AQ.Ab8RN6JowELmm7EjMG3Dm0sdHUldfClMwQfUPZbckHVOWuAQsw"  # aistudio.google.com dan olgan kalit
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

app = Flask(__name__)


# ============ BAZA ============
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,        -- 'income' yoki 'expense'
            amount REAL NOT NULL,
            category TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            person_name TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT,              -- 'YYYY-MM-DD' yoki NULL
            status TEXT NOT NULL DEFAULT 'active',  -- 'active' yoki 'paid'
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ============ TELEGRAM YORDAMCHI ============
MAIN_KEYBOARD = {
    "keyboard": [
        ["📊 Balans", "📌 Qarzlar"],
        ["🧾 Hisobot", "🤖 AI tahlil"],
        ["❓ Yordam"],
    ],
    "resize_keyboard": True
}


def send_message(chat_id, text):
    requests.post(f"{API_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": MAIN_KEYBOARD
    })


def fmt(n):
    return f"{n:,.0f}".replace(",", " ")


# ============ LOGIKA ============
def add_transaction(user_id, ttype, amount, category):
    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (user_id, type, amount, category, created_at) VALUES (?,?,?,?,?)",
        (user_id, ttype, amount, category, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_balance(user_id):
    conn = get_db()
    income = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE user_id=? AND type='income'", (user_id,)
    ).fetchone()["s"]
    expense = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE user_id=? AND type='expense'", (user_id,)
    ).fetchone()["s"]
    conn.close()
    return income, expense


def get_report_by_category(user_id, ttype):
    conn = get_db()
    rows = conn.execute(
        """SELECT COALESCE(category,'boshqa') cat, SUM(amount) total
           FROM transactions WHERE user_id=? AND type=?
           GROUP BY cat ORDER BY total DESC""",
        (user_id, ttype)
    ).fetchall()
    conn.close()
    return rows


def reset_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# ============ AI TAHLIL ============
def get_ai_advice(user_id):
    income, expense = get_balance(user_id)
    balance = income - expense
    exp_rows = get_report_by_category(user_id, "expense")
    inc_rows = get_report_by_category(user_id, "income")
    debts = get_active_debts(user_id)

    exp_text = "\n".join([f"- {r['cat']}: {fmt(r['total'])} so'm" for r in exp_rows]) or "yo'q"
    inc_text = "\n".join([f"- {r['cat']}: {fmt(r['total'])} so'm" for r in inc_rows]) or "yo'q"
    debt_total = sum(d["amount"] for d in debts)

    prompt = (
        "Sen moliyaviy maslahatchisan. Foydalanuvchining quyidagi shaxsiy byudjet "
        "ma'lumotlariga qarab, o'zbek tilida, oddiy va tushunarli qilib, 4-6 jumlali "
        "qisqa amaliy maslahat ber. Raqamlarni takrorlama, faqat xulosa va tavsiya ber.\n\n"
        f"Umumiy daromad: {fmt(income)} so'm\n"
        f"Umumiy xarajat: {fmt(expense)} so'm\n"
        f"Qoldiq: {fmt(balance)} so'm\n"
        f"Daromad turlari:\n{inc_text}\n"
        f"Xarajat turlari:\n{exp_text}\n"
        f"Boshqalardan olishi kerak bo'lgan qarzlar jami: {fmt(debt_total)} so'm\n"
    )

    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20
        )
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return "⚠️ AI tahlil hozircha ishlamadi. GEMINI_API_KEY to'g'ri kiritilganini tekshir."


# ============ OVOZLI XABAR -> MATN ============
import base64


def transcribe_voice(file_id):
    # Telegramdan fayl manzilini olish
    r = requests.get(f"{API_URL}/getFile", params={"file_id": file_id}, timeout=15)
    file_path = r.json()["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

    # Audio faylni yuklab olish
    audio_bytes = requests.get(file_url, timeout=15).content
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    prompt = (
        "Bu ovozli xabar — foydalanuvchi shaxsiy xarajat/daromadini aytmoqda. "
        "Xabarni tingla va quyidagi qat'iy formatda javob ber, boshqa hech narsa yozma:\n"
        "- Agar daromad haqida bo'lsa (pul oldi, ish haqi, sotdi va h.k.): +MIQDOR KATEGORIYA\n"
        "- Agar xarajat haqida bo'lsa (pul sarfladi, sotib oldi, to'ladi va h.k.): -MIQDOR KATEGORIYA\n"
        "MIQDOR faqat raqamlardan iborat bo'lsin (probel, vergulsiz). "
        "KATEGORIYA — bir yoki ikki so'zli qisqa o'zbekcha nom (masalan: oylik, taksi, ovqat, kommunal).\n"
        "Agar xabar xarajat/daromad haqida bo'lmasa yoki tushunarsiz bo'lsa, faqat NONE deb yoz.\n\n"
        "Misollar:\n"
        "\"besh yuz ming so'm oylik oldim\" -> +500000 oylik\n"
        "\"yigirma besh ming taksiga berdim\" -> -25000 taksi\n"
    )

    try:
        resp = requests.post(
            GEMINI_URL,
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}}
                    ]
                }]
            },
            timeout=30
        )
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


# ============ QARZLAR ============
def add_debt(user_id, name, amount, due_date):
    conn = get_db()
    conn.execute(
        "INSERT INTO debts (user_id, person_name, amount, due_date, status, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, name, amount, due_date, "active", datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_active_debts(user_id):
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM debts WHERE user_id=? AND status='active'
           ORDER BY (due_date IS NULL), due_date""",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows


def pay_debt(user_id, name):
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM debts WHERE user_id=? AND status='active' AND person_name=?
           ORDER BY due_date LIMIT 1""",
        (user_id, name)
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("UPDATE debts SET status='paid' WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    return True


def parse_debt_add(text):
    # +qarz Ism 100000 15.08.2026   (sana ixtiyoriy)
    m = re.match(r"^\+qarz\s+(\S+)\s+([\d\s.,]+)(?:\s+(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?)?$", text.strip())
    if not m:
        return None
    name, amount_str, day, month, year = m.groups()
    amount_str = amount_str.replace(" ", "").replace(",", "").replace(".", "")
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    due_date = None
    if day and month:
        today = datetime.now()
        yr = int(year) if year else today.year
        if yr < 100:
            yr += 2000
        try:
            due = datetime(yr, int(month), int(day))
            if not year and due.date() < today.date():
                due = datetime(yr + 1, int(month), int(day))
            due_date = due.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return name, amount, due_date


# ============ XABAR PARSER ============
# Foydalanuvchi yozadi:
#   +500000 oylik      -> daromad
#   -25000 taksi        -> xarajat
AMOUNT_RE = re.compile(r"^([+-])\s*([\d\s.,]+)\s*(.*)$")


def parse_entry(text):
    m = AMOUNT_RE.match(text.strip())
    if not m:
        return None
    sign, raw_amount, category = m.groups()
    amount_str = raw_amount.replace(" ", "").replace(",", "").replace(".", "")
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    ttype = "income" if sign == "+" else "expense"
    category = category.strip() or "boshqa"
    return ttype, amount, category


HELP_TEXT = (
    "💰 <b>Byudjet boti</b>\n\n"
    "Kirim/chiqim:\n"
    "  <code>+500000 oylik</code> — daromad qo'shish\n"
    "  <code>-25000 taksi</code> — xarajat qo'shish\n"
    "/balance — umumiy holat\n"
    "/report — kategoriya bo'yicha hisobot\n"
    "/reset — barcha yozuvlarni tozalash\n\n"
    "📌 <b>Qarzlar</b> (senga qaytarishi kerak bo'lganlar):\n"
    "  <code>+qarz Vali 100000 15.08</code> — qarz qo'shish (sana ixtiyoriy)\n"
    "  <code>-qarz Vali</code> — qarzni to'landi deb belgilash\n"
    "/qarzlar — faol qarzlar ro'yxati\n\n"
    "🤖 /tahlil — AI orqali moliyaviy maslahat olish\n"
    "🎙 Yozish o'rniga ovozli xabar ham yuborsang bo'ladi, masalan:\n"
    "  \"Besh yuz ming so'm oylik oldim\" yoki \"Yigirma besh ming taksiga berdim\"\n"
)


# ============ WEBHOOK ============
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    msg = update.get("message")
    if not msg:
        return "ok"

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]

    if "voice" in msg:
        send_message(chat_id, "🎙 Ovozli xabar eshitilmoqda...")
        text = transcribe_voice(msg["voice"]["file_id"])
        if not text or text.strip().upper() == "NONE":
            send_message(chat_id, "⚠️ Tushuna olmadim. Masalan: \"besh yuz ming so'm oylik oldim\" deb ayt.")
            return "ok"
        send_message(chat_id, f"🎙 Tushundim: <code>{text}</code>")
    else:
        text = msg.get("text", "").strip()

    handle_text(chat_id, user_id, text)
    return "ok"


def handle_text(chat_id, user_id, text):
    if text in ("/start", "/help", "❓ Yordam"):
        send_message(chat_id, HELP_TEXT)

    elif text in ("/balance", "📊 Balans"):
        income, expense = get_balance(user_id)
        balance = income - expense
        send_message(chat_id,
            f"📊 <b>Holat</b>\n"
            f"Daromad: {fmt(income)} so'm\n"
            f"Xarajat: {fmt(expense)} so'm\n"
            f"Qoldiq: <b>{fmt(balance)} so'm</b>"
        )

    elif text in ("/report", "🧾 Hisobot"):
        inc_rows = get_report_by_category(user_id, "income")
        exp_rows = get_report_by_category(user_id, "expense")
        parts = ["📈 <b>Daromadlar:</b>"]
        parts += [f"  {r['cat']}: {fmt(r['total'])} so'm" for r in inc_rows] or ["  (yo'q)"]
        parts.append("\n📉 <b>Xarajatlar:</b>")
        parts += [f"  {r['cat']}: {fmt(r['total'])} so'm" for r in exp_rows] or ["  (yo'q)"]
        send_message(chat_id, "\n".join(parts))

    elif text in ("/reset",):
        reset_user(user_id)
        send_message(chat_id, "✅ Barcha yozuvlar o'chirildi.")

    elif text in ("/tahlil", "🤖 AI tahlil"):
        send_message(chat_id, "🤖 Tahlil qilinmoqda, biroz kuting...")
        advice = get_ai_advice(user_id)
        send_message(chat_id, f"🤖 <b>AI tahlil:</b>\n\n{advice}")

    elif text in ("/qarzlar", "📌 Qarzlar"):
        rows = get_active_debts(user_id)
        if not rows:
            send_message(chat_id, "Faol qarzlar yo'q ✅")
        else:
            today = datetime.now().date()
            lines = ["📌 <b>Faol qarzlar:</b>"]
            for r in rows:
                due = r["due_date"]
                if due:
                    due_dt = datetime.strptime(due, "%Y-%m-%d").date()
                    mark = " ⚠️ MUDDATI O'TDI" if due_dt < today else f" (muddat: {due_dt.strftime('%d.%m.%Y')})"
                else:
                    mark = ""
                lines.append(f"  {r['person_name']}: {fmt(r['amount'])} so'm{mark}")
            send_message(chat_id, "\n".join(lines))

    elif text.startswith("+qarz"):
        parsed = parse_debt_add(text)
        if parsed:
            name, amount, due_date = parsed
            add_debt(user_id, name, amount, due_date)
            due_str = f", muddat: {due_date}" if due_date else ""
            send_message(chat_id, f"✅ Qarz qo'shildi: {name} — {fmt(amount)} so'm{due_str}")
        else:
            send_message(chat_id, "Format: <code>+qarz Ism 100000 15.08</code>")

    elif text.startswith("-qarz"):
        name = text.replace("-qarz", "").strip()
        if name and pay_debt(user_id, name):
            send_message(chat_id, f"✅ {name} ning qarzi to'landi deb belgilandi.")
        else:
            send_message(chat_id, f"'{name}' nomli faol qarz topilmadi.")

    else:
        parsed = parse_entry(text)
        if parsed:
            ttype, amount, category = parsed
            add_transaction(user_id, ttype, amount, category)
            label = "Daromad" if ttype == "income" else "Xarajat"
            send_message(chat_id, f"✅ {label} qo'shildi: {fmt(amount)} so'm ({category})")
        else:
            send_message(chat_id, "Tushunmadim 🤔\n" + HELP_TEXT)


@app.route("/")
def index():
    return "Bot ishlayapti"


if __name__ == "__main__":
    init_db()
    app.run()
else:
    init_db()
  
