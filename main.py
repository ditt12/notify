import asyncio
import re
import os
import sys
import json
import getpass
import logging
from datetime import datetime
from pathlib import Path


# ── Dependency check ──────────────────────────────────────────────────────────
def check_deps():
    missing = []
    for pkg in [("telethon", "telethon"), ("colorama", "colorama"), ("dotenv", "python-dotenv")]:
        try:
            __import__(pkg[0])
        except ImportError:
            missing.append(pkg[1])
    if missing:
        print(f"\n❌  Package belum terinstall: {', '.join(missing)}")
        print(f"    Jalankan: pip install {' '.join(missing)}\n")
        sys.exit(1)

check_deps()

from telethon import TelegramClient, events
from telethon.tl.types import User, MessageService
from telethon.errors import FloodWaitError
from colorama import Fore, Style, init as colorama_init
from dotenv import set_key, dotenv_values

colorama_init(autoreset=True)

# ── Path constants ────────────────────────────────────────────────────────────
ENV_FILE  = Path(".env")
SESSION   = "userbot_session"
LOG_FILE  = "keyword_matches.log"

# ── Color helpers ─────────────────────────────────────────────────────────────
def c(txt, col):  return f"{col}{txt}{Style.RESET_ALL}"
def ok(t):   print(c(f"  ✅ {t}", Fore.GREEN))
def info(t): print(c(f"  ℹ  {t}", Fore.CYAN))
def warn(t): print(c(f"  ⚠  {t}", Fore.YELLOW))
def err(t):  print(c(f"  ❌ {t}", Fore.RED))
def dim(t):  print(c(f"     {t}", Fore.WHITE))
def head(t): print(c(f"\n{'━'*52}\n  {t}\n{'━'*52}", Fore.MAGENTA))


def banner():
    print(c("""
╔════════════════════════════════════════════════════╗
║                                                    ║
║   🤖  TELEGRAM USERBOT KEYWORD NOTIFIER  v2.0     ║
║       Monitor All Channels · Auto Notify           ║
║                                                    ║
╚════════════════════════════════════════════════════╝""", Fore.CYAN))


# ══════════════════════════════════════════════════════════════════════════════
#  .ENV HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def save_env(key: str, value: str):
    """Tulis atau update satu key di .env."""
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    set_key(str(ENV_FILE), key, value)


def load_env() -> dict:
    """Baca semua nilai dari .env."""
    if not ENV_FILE.exists():
        return {}
    return dotenv_values(str(ENV_FILE))


def env_complete(cfg: dict) -> bool:
    """Cek apakah semua key wajib sudah ada."""
    required = ["API_ID", "API_HASH", "PHONE", "NOTIFY_TARGET", "KEYWORDS"]
    return all(cfg.get(k, "").strip() for k in required)


# ══════════════════════════════════════════════════════════════════════════════
#  SETUP WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def ask(label: str, default: str = "", secret: bool = False, hint: str = "") -> str:
    """Prompt dengan tampilan konsisten."""
    parts = [c(f"  ➤  {label}", Fore.YELLOW)]
    if hint:
        parts.append(c(f" [{hint}]", Fore.WHITE))
    if default:
        parts.append(c(f" (enter = {default!r})", Fore.WHITE))
    parts.append(c(": ", Fore.YELLOW))
    prompt_str = "".join(parts)
    if secret:
        val = getpass.getpass(prompt_str)
    else:
        val = input(prompt_str).strip()
    return val if val else default


def setup_wizard() -> dict:
    head("🔧 SETUP WIZARD")
    print(c("  Semua input akan disimpan otomatis ke .env\n", Fore.WHITE))

    cfg = load_env()

    # ── Credentials ───────────────────────────────────────────────────────────
    print(c("\n  ① TELEGRAM API CREDENTIALS", Fore.CYAN))
    dim("Buka https://my.telegram.org/apps → buat App → salin API_ID & API_HASH\n")

    api_id   = ask("API_ID",   cfg.get("API_ID",   ""), hint="contoh: 12345678")
    api_hash = ask("API_HASH", cfg.get("API_HASH", ""), hint="32 karakter hex", secret=True)
    phone    = ask("PHONE",    cfg.get("PHONE",    ""), hint="contoh: +628123456789")

    save_env("API_ID",   api_id)
    save_env("API_HASH", api_hash)
    save_env("PHONE",    phone)

    # ── Notify Target ─────────────────────────────────────────────────────────
    print(c("\n  ② NOTIFY TARGET", Fore.CYAN))
    dim("Ke mana notifikasi dikirim saat keyword ditemukan?\n")
    print(c("  Contoh nilai:", Fore.WHITE))
    print(c("    me                  → Saved Messages (diri sendiri)", Fore.WHITE))
    print(c("    @username           → username Telegram orang lain", Fore.WHITE))
    print(c("    -100xxxxxxxxxx      → Chat ID grup / channel\n", Fore.WHITE))

    notify_target = ask("NOTIFY_TARGET", cfg.get("NOTIFY_TARGET", "me"),
                        hint="me / @username / chat_id")
    save_env("NOTIFY_TARGET", notify_target)

    # ── Keywords ──────────────────────────────────────────────────────────────
    print(c("\n  ③ KEYWORDS", Fore.CYAN))
    dim("Pisahkan dengan koma. Case-insensitive. Contoh: jual,beli,WTS,flash sale\n")

    default_kw = cfg.get("KEYWORDS", "jual,beli,WTS,WTB,flash sale,diskon,promo")
    keywords_raw = ask("KEYWORDS", default_kw, hint="kata1,kata2,...")
    save_env("KEYWORDS", keywords_raw)

    # ── Cooldown ──────────────────────────────────────────────────────────────
    print(c("\n  ④ COOLDOWN ANTI-SPAM", Fore.CYAN))
    dim("Jeda minimum (detik) sebelum notif ulang keyword+chat yang sama.\n")

    cooldown = ask("COOLDOWN_SECONDS", cfg.get("COOLDOWN_SECONDS", "30"), hint="detik")
    save_env("COOLDOWN_SECONDS", cooldown)

    ok(f"Konfigurasi tersimpan di {ENV_FILE}\n")

    return parse_config(dotenv_values(str(ENV_FILE)))


def parse_config(cfg: dict) -> dict:
    """Ubah dict .env menjadi config siap pakai."""
    return {
        "api_id":        int(cfg.get("API_ID",  0)),
        "api_hash":      cfg.get("API_HASH",  ""),
        "phone":         cfg.get("PHONE",     ""),
        "notify_target": cfg.get("NOTIFY_TARGET", "me").strip(),
        "keywords":      [k.strip() for k in cfg.get("KEYWORDS", "").split(",") if k.strip()],
        "cooldown":      int(cfg.get("COOLDOWN_SECONDS", 30) or 30),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  BOT LOGIC
# ══════════════════════════════════════════════════════════════════════════════

_cooldown: dict = {}
stats = {"scanned": 0, "matched": 0, "start": datetime.now()}

NOTIFY_TPL = (
    "🔔 **KEYWORD ALERT**\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🔑 Keyword  : `{keyword}`\n"
    "👤 Dari     : {sender}\n"
    "💬 Chat     : {chat}\n"
    "🕐 Waktu    : {time}\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📝 **Pesan:**\n{message}\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🔗 [Buka Pesan]({link})"
)


def in_cooldown(chat_id: int, kw: str, secs: int) -> bool:
    key = (chat_id, kw.lower())
    if key in _cooldown:
        return (datetime.now() - _cooldown[key]).total_seconds() < secs
    return False


def set_cooldown(chat_id: int, kw: str):
    _cooldown[(chat_id, kw.lower())] = datetime.now()


def match_keywords(text: str, keywords: list) -> list:
    found, tl = [], text.lower()
    for kw in keywords:
        pat = re.escape(kw.lower())
        if len(kw) >= 3:
            if re.search(r'\b' + pat + r'\b', tl):
                found.append(kw)
        else:
            if pat in tl:
                found.append(kw)
    return found


async def get_link(client, event) -> str:
    try:
        chat = await event.get_chat()
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}/{event.message.id}"
        cid = str(event.chat_id).replace("-100", "")
        return f"https://t.me/c/{cid}/{event.message.id}"
    except Exception:
        return "—"


async def get_sender_str(event) -> str:
    try:
        s = await event.get_sender()
        if isinstance(s, User):
            name = " ".join(filter(None, [s.first_name, s.last_name]))
            return f"{name} (@{s.username})" if s.username else name
        if hasattr(s, "title"):
            return s.title
    except Exception:
        pass
    return "Unknown"


async def get_chat_str(event) -> str:
    try:
        chat = await event.get_chat()
        if hasattr(chat, "title"):
            return chat.title
        if isinstance(chat, User):
            return " ".join(filter(None, [chat.first_name, chat.last_name])) or "DM"
    except Exception:
        pass
    return "Unknown"


async def send_notify(client, event, cfg, keyword, sender, chat_title, link):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    text = (event.message.text or "")[:500]

    msg = NOTIFY_TPL.format(
        keyword=keyword, sender=sender, chat=chat_title,
        time=now, message=text, link=link,
    )

    # Resolve target: integer jika chat_id, string jika @username / "me"
    raw = cfg["notify_target"]
    target = int(raw) if raw.lstrip("-").isdigit() else raw

    try:
        await client.send_message(target, msg, link_preview=False)
        stats["matched"] += 1
        ok(f"MATCH '{keyword}' │ {chat_title[:32]} │ {sender[:28]}")

        # Tulis log
        entry = {"time": now, "keyword": keyword, "chat": chat_title,
                 "sender": sender, "preview": text[:200], "link": link}
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    except FloodWaitError as e:
        warn(f"FloodWait {e.seconds}s — menunggu...")
        await asyncio.sleep(e.seconds)
        await client.send_message(target, msg, link_preview=False)
    except Exception as ex:
        err(f"Gagal kirim notif: {ex}")


async def stats_loop():
    while True:
        await asyncio.sleep(300)
        up = datetime.now() - stats["start"]
        h, rem = divmod(int(up.total_seconds()), 3600)
        m, _ = divmod(rem, 60)
        print(c(
            f"\n  📊 STATS  Uptime: {h}j {m}m  │  "
            f"Dipindai: {stats['scanned']}  │  Match: {stats['matched']}\n",
            Fore.CYAN
        ))


async def run_bot(cfg: dict):
    head("🚀 MENJALANKAN BOT")

    client = TelegramClient(SESSION, cfg["api_id"], cfg["api_hash"])

    info(f"Menghubungkan dengan {cfg['phone']} ...")
    await client.start(phone=cfg["phone"])

    me = await client.get_me()
    ok(f"Login sebagai : {me.first_name} (@{me.username or 'no-username'})")

    # Tampilkan info target notif
    raw_target = cfg["notify_target"]
    try:
        entity = await client.get_entity(
            int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
        )
        if isinstance(entity, User):
            t_str = f"{entity.first_name} (@{entity.username or '?'})"
        elif hasattr(entity, "title"):
            t_str = entity.title
        else:
            t_str = raw_target
    except Exception:
        t_str = "Saved Messages" if raw_target == "me" else raw_target

    print(c(f"\n  ⚙  CONFIG AKTIF", Fore.MAGENTA))
    print(c(f"     🔍 Keywords   : {', '.join(cfg['keywords'])}", Fore.WHITE))
    print(c(f"     👁  Monitor    : SEMUA channel, grup & DM", Fore.WHITE))
    print(c(f"     🔔 Notify ke  : {t_str}", Fore.WHITE))
    print(c(f"     ⏱  Cooldown   : {cfg['cooldown']} detik", Fore.WHITE))
    print(c(f"     📄 Log file   : {LOG_FILE}\n", Fore.WHITE))

    @client.on(events.NewMessage)
    async def handler(event):
        try:
            if isinstance(event.message, MessageService):
                return
            if not event.message or not event.message.text:
                return

            stats["scanned"] += 1
            matched = match_keywords(event.message.text, cfg["keywords"])
            if not matched:
                return

            chat_title = await get_chat_str(event)
            sender     = await get_sender_str(event)
            link       = await get_link(client, event)
            chat_id    = event.chat_id

            for kw in matched:
                if in_cooldown(chat_id, kw, cfg["cooldown"]):
                    continue
                set_cooldown(chat_id, kw)
                await send_notify(client, event, cfg, kw, sender, chat_title, link)

        except Exception as ex:
            logging.debug(f"Handler error: {ex}")

    asyncio.create_task(stats_loop())

    ok("Bot aktif! Memantau SEMUA chat...")
    print(c("     Tekan Ctrl+C untuk berhenti.\n", Fore.WHITE))

    await client.run_until_disconnected()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    banner()

    raw_cfg = load_env()

    if env_complete(raw_cfg):
        cfg = parse_config(raw_cfg)
        print(c(f"\n  ✅ Config ditemukan di {ENV_FILE}", Fore.GREEN))
        print(c(f"     Phone    : {cfg['phone']}", Fore.WHITE))
        print(c(f"     Target   : {cfg['notify_target']}", Fore.WHITE))
        kw_preview = ", ".join(cfg["keywords"][:5])
        if len(cfg["keywords"]) > 5:
            kw_preview += f", ... (+{len(cfg['keywords'])-5} lagi)"
        print(c(f"     Keywords : {kw_preview}", Fore.WHITE))

        ans = input(c("\n  ➤  Gunakan config ini? [Y=lanjut / n=setup ulang]: ", Fore.YELLOW)).strip().lower()
        if ans in ("n", "no", "tidak", "ulang"):
            cfg = setup_wizard()
    else:
        warn(f"File {ENV_FILE} tidak ditemukan atau belum lengkap.")
        info("Jalankan setup wizard dulu...\n")
        cfg = setup_wizard()

    await run_bot(cfg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        up = datetime.now() - stats["start"]
        h, rem = divmod(int(up.total_seconds()), 3600)
        m, _ = divmod(rem, 60)
        print(c(
            f"\n\n  ⛔ Bot dihentikan  │  Uptime: {h}j {m}m  │  "
            f"Dipindai: {stats['scanned']}  │  Match: {stats['matched']}\n",
            Fore.RED
        ))
    except Exception as e:
        err(f"Fatal error: {e}")
        raise
