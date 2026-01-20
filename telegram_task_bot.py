import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request

URL_RE = re.compile(r"https?://[^\s]+")

def load_env_file(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("BOT_TOKEN is required")

DB_PATH = os.environ.get("BOT_DB_PATH", "telegram_task_bot.sqlite3")
POLL_INTERVAL = float(os.environ.get("BOT_POLL_INTERVAL", "2.0"))
DEFAULT_EMOJI = os.environ.get("BOT_DEFAULT_EMOJI", "🪐")


def now_ts():
    return int(time.time())


def start_of_today_ts():
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))


def api_call(method, params=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    payload = json.loads(body)
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["result"]


def send_message(chat_id, text, reply_markup=None, reply_to_message_id=None):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
    return api_call("sendMessage", params)


def edit_message(chat_id, message_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    return api_call("editMessageText", params)


def answer_callback_query(callback_query_id, text):
    return api_call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, emoji TEXT, created_at INTEGER, updated_at INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, creator_id INTEGER, creator_name TEXT, title TEXT, detail TEXT, link TEXT, status TEXT, created_at INTEGER, claimed_by INTEGER, claimed_by_name TEXT, claimed_at INTEGER, completed_at INTEGER, message_id INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, user_id INTEGER, user_name TEXT, claimed_at INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (chat_id INTEGER, user_id INTEGER, username TEXT, first_name TEXT, x_handle TEXT, muted INTEGER DEFAULT 0, pending_backlink_task_id INTEGER DEFAULT NULL, created_at INTEGER, updated_at INTEGER, PRIMARY KEY (chat_id, user_id))"
        )
        # 确保现有表也有此列
        try:
            conn.execute("ALTER TABLE users ADD COLUMN pending_backlink_task_id INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def set_user_pending_backlink(chat_id, user_id, task_id):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET pending_backlink_task_id = ?, updated_at = ? WHERE chat_id = ? AND user_id = ?",
            (task_id, ts, chat_id, user_id),
        )
        conn.commit()


def get_user_pending_backlink(chat_id, user_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT pending_backlink_task_id FROM users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return row[0] if row else None


def get_chat_emoji(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT emoji FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        if row and row[0]:
            return row[0]
        return DEFAULT_EMOJI


def set_chat_emoji(chat_id, emoji):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO chats (chat_id, emoji, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET emoji = excluded.emoji, updated_at = excluded.updated_at",
            (chat_id, emoji, ts, ts),
        )
        conn.commit()


def upsert_user(chat_id, user):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO users (chat_id, user_id, username, first_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET username = excluded.username, first_name = excluded.first_name, updated_at = excluded.updated_at",
            (
                chat_id,
                user.get("id"),
                user.get("username"),
                user.get("first_name"),
                ts,
                ts,
            ),
        )
        conn.commit()


def set_user_x_handle(chat_id, user_id, x_handle):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET x_handle = ?, updated_at = ? WHERE chat_id = ? AND user_id = ?",
            (x_handle, ts, chat_id, user_id),
        )
        conn.commit()


def is_user_bound(chat_id, user_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT x_handle FROM users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return bool(row and row[0])


def set_user_muted(chat_id, user_id, muted):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET muted = ?, updated_at = ? WHERE chat_id = ? AND user_id = ?",
            (1 if muted else 0, ts, chat_id, user_id),
        )
        conn.commit()


def list_all_users():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT user_id, muted FROM users",
        ).fetchall()
        return rows


def create_task(chat_id, creator_id, creator_name, title, detail, link):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (chat_id, creator_id, creator_name, title, detail, link, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (chat_id, creator_id, creator_name, title, detail, link, ts),
        )
        conn.commit()
        return cur.lastrowid


def count_boosts_today(creator_id):
    start_ts = start_of_today_ts()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(1) FROM tasks WHERE creator_id = ? AND title = ? AND created_at >= ?",
            (creator_id, "发车互助", start_ts),
        ).fetchone()
        return row[0] if row else 0


def set_task_message_id(task_id, message_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tasks SET message_id = ? WHERE id = ?", (message_id, task_id))
        conn.commit()


def fetch_task(task_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, chat_id, creator_id, creator_name, title, detail, link, status, created_at, claimed_by, claimed_by_name, claimed_at, completed_at, message_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        keys = [
            "id",
            "chat_id",
            "creator_id",
            "creator_name",
            "title",
            "detail",
            "link",
            "status",
            "created_at",
            "claimed_by",
            "claimed_by_name",
            "claimed_at",
            "completed_at",
            "message_id",
        ]
        return dict(zip(keys, row))


def list_open_tasks(chat_id=None, limit=10):
    with sqlite3.connect(DB_PATH) as conn:
        if chat_id is None:
            rows = conn.execute(
                "SELECT id, title, status FROM tasks WHERE status IN ('open', 'claimed') ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, status FROM tasks WHERE chat_id = ? AND status IN ('open', 'claimed') ORDER BY created_at DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return rows


def count_task_claims(task_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(1) FROM task_claims WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row[0] if row else 0


def claim_task(task_id, user_id, user_name):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row[0] == "done":
            return "closed"
        dup = conn.execute(
            "SELECT 1 FROM task_claims WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        ).fetchone()
        if dup:
            return "duplicate"
        conn.execute(
            "INSERT INTO task_claims (task_id, user_id, user_name, claimed_at) VALUES (?, ?, ?, ?)",
            (task_id, user_id, user_name, ts),
        )
        if row[0] == "open":
            conn.execute(
                "UPDATE tasks SET status = 'claimed', claimed_by = ?, claimed_by_name = ?, claimed_at = ? WHERE id = ?",
                (user_id, user_name, ts, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET claimed_by = ?, claimed_by_name = ?, claimed_at = ? WHERE id = ?",
                (user_id, user_name, ts, task_id),
            )
        conn.commit()
        return "ok"


def release_task(task_id, user_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row[0] == "done":
            return False
        deleted = conn.execute(
            "DELETE FROM task_claims WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        ).rowcount
        if not deleted:
            return False
        remaining = conn.execute(
            "SELECT COUNT(1) FROM task_claims WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        remaining_count = remaining[0] if remaining else 0
        if remaining_count == 0:
            conn.execute(
                "UPDATE tasks SET status = 'open', claimed_by = NULL, claimed_by_name = NULL, claimed_at = NULL WHERE id = ?",
                (task_id,),
            )
        conn.commit()
        return True


def complete_task(task_id, user_id):
    ts = now_ts()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row[0] == "done":
            return False
        claimed = conn.execute(
            "SELECT 1 FROM task_claims WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        ).fetchone()
        if not claimed:
            return False
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (ts, task_id),
        )
        conn.commit()
        return True


def build_task_text(task, emoji):
    claim_count = count_task_claims(task["id"])
    lines = [
        f"任务 #{task['id']}",
        f"发布者: {task['creator_name']}",
        f"标题: {task['title'] or '-'}",
    ]
    if task.get("detail"):
        lines.append(f"说明: {task['detail']}")
    if task.get("link"):
        lines.append(f"链接: {task['link']}")
    lines.append(f"暗号: {emoji}")
    lines.append("规则：评论带暗号才是自己人，可优先回复互动")
    if task["status"] == "open":
        lines.append("状态: 待接单")
    elif task["status"] == "claimed":
        lines.append(f"状态: 已接单（{claim_count}人）")
    else:
        lines.append("状态: 已完成")
    return "\n".join(lines)


def keyboard_for_status(status, task_id):
    if status == "open":
        return {"inline_keyboard": [[{"text": "接单", "callback_data": f"claim:{task_id}"}]]}
    if status == "claimed":
        return {
            "inline_keyboard": [
                [
                    {"text": "完成", "callback_data": f"done:{task_id}"},
                    {"text": "放弃", "callback_data": f"release:{task_id}"},
                ]
            ]
        }
    return None


def display_name(user):
    if user.get("username"):
        return f"@{user['username']}"
    return user.get("first_name") or "Unknown"


def ensure_bound(chat_id, user_id):
    if is_user_bound(chat_id, user_id):
        return True
    message = "\n".join(
        [
            "请先绑定你的 X 账号：",
            "/bind @your_handle",
            "",
            "绑定后才能发车或接单。",
        ]
    )
    send_message(chat_id, message)
    return False


def safe_send_message(chat_id, text, reply_markup=None):
    try:
        return send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        return None


def extract_first_url(text):
    match = URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0).strip("`<>")
    url = url.rstrip(").,，。！？】）]}>'\"")
    return url or None


def broadcast_task(task, emoji, reply_markup, creator_id):
    text = build_task_text(task, emoji)
    users = list_all_users()
    first_result = None
    for user_id, muted in users:
        if muted:
            continue
        result = safe_send_message(user_id, text, reply_markup=reply_markup)
        if not first_result and result:
            first_result = result
    return first_result


def handle_message(message):
    chat_id = message["chat"]["id"]
    chat_type = message["chat"].get("type")
    text = message.get("text", "").strip()
    user = message.get("from", {})
    name = display_name(user)
    upsert_user(chat_id, user)

    if chat_type != "private":
        send_message(chat_id, "请私聊机器人使用所有功能。")
        return

    # 处理回链收集逻辑
    if not text.startswith("/"):
        pending_task_id = get_user_pending_backlink(chat_id, user.get("id"))
        if pending_task_id:
            task = fetch_task(pending_task_id)
            if task:
                creator_id = task.get("creator_id")
                if creator_id:
                    backlink = extract_first_url(text) or text
                    msg = "\n".join(
                        [
                            f"任务 #{pending_task_id} 回链：{backlink}",
                            "",
                            "接单者已完成互动，请尽快回复他以扩大算法曝光权重，尽快提升你的帖子进入更大的流量池。",
                        ]
                    )
                    safe_send_message(creator_id, msg)
                    send_message(chat_id, "回链已发送给车头！感谢你的互助。")
                    set_user_pending_backlink(chat_id, user.get("id"), None)
                    return
            send_message(chat_id, "请发送回链（评论或互动链接）")
            return

    if not text.startswith("/"):
        return

    parts = text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command in ("/start", "/help"):
        help_text = "\n".join(
            [
                "🤖 X 车队算法互助机器人", 

                "目标：通过车队互助，助力 X 用户跳出算法门槛。",
                "高粉账号获得评论与互动，突破流量池；低粉账号获得回复与曝光，水涨船高。",
                "建议使用 X 算法模拟器优化推文：x.com",
                "",
                "基础流程：",
                "1、绑定 X 账号： /bind @your_handle 绑定 X 账号",
                "2、发车或接单： /boost <链接> [一句话目标]（每日最多 10 次）",
                "3、带暗号评论互动：/setemoji 😊 设置暗号",
                "4、车头应优先回复带暗号的队友 （自己人）",
                "",
                "/mute 静音通知",
                "/unmute 恢复通知",
            ]
        )
        send_message(chat_id, help_text)
        return

    if command == "/bind":
        if not args:
            send_message(chat_id, "请输入你的 X 账号，例如：/bind @your_handle")
            return
        x_handle = args.strip()
        if not x_handle.startswith("@"):
            x_handle = f"@{x_handle}"
        set_user_x_handle(chat_id, user.get("id"), x_handle)
        send_message(chat_id, f"绑定成功：{x_handle}")

        # 全局通知互关
        users = list_all_users()
        user_count = len(users)
        x_link = f"https://x.com/{x_handle.lstrip('@')}"
        broadcast_msg = (
            f"📢 新队友加入互助！\n\n"
            f"你是第 {user_count} 位队友！\n\n"
            f"用户 {name} 已绑定 X 账号：{x_handle}\n"
            f"主页链接：{x_link}\n\n"
            f"大家快去关注他吧！建立互关关系可以显著提升算法推荐权重，实现共同涨粉。\n\n X 新算法更喜欢互动的人，而不是粉丝数量多的人 ！！"
        )
        
        for other_user_id, muted in users:
            if other_user_id != user.get("id"):
                safe_send_message(other_user_id, broadcast_msg)
        return

    if command == "/mute":
        set_user_muted(chat_id, user.get("id"), True)
        send_message(chat_id, "已静音通知")
        return

    if command == "/unmute":
        set_user_muted(chat_id, user.get("id"), False)
        send_message(chat_id, "已恢复通知")
        return

    if command == "/setemoji":
        if not args:
            send_message(chat_id, "请输入一个 Emoji 作为暗号")
            return
        set_chat_emoji(chat_id, args.strip())
        send_message(chat_id, f"已设置暗号为：{args.strip()}")
        return

    if command == "/boost":
        if not ensure_bound(chat_id, user.get("id")):
            return
        if not args:
            send_message(chat_id, "请输入推文链接，例如：/boost https://x.com/xxx/status/123")
            return
        current_count = count_boosts_today(user.get("id"))
        if current_count >= 10:
            send_message(chat_id, "今日发车次数已达上限（10 次），请明日再试")
            return
        parts = args.split(maxsplit=1)
        link = parts[0].strip().strip("`<>")
        detail = parts[1] if len(parts) > 1 else ""
        task_id = create_task(chat_id, user.get("id"), name, "发车互助", detail, link)
        emoji = get_chat_emoji(chat_id)
        task = fetch_task(task_id)
        kb = keyboard_for_status("open", task_id)
        result = broadcast_task(task, emoji, kb, user.get("id"))
        set_task_message_id(task_id, result.get("message_id"))
        return

    if command == "/task":
        if not ensure_bound(chat_id, user.get("id")):
            return
        if not args:
            send_message(chat_id, "请输入任务标题，例如：/task 设计海报 | 周五前提交 | https://example.com")
            return
        fields = [s.strip() for s in args.split("|")]
        title = fields[0] if len(fields) >= 1 else ""
        detail = fields[1] if len(fields) >= 2 else ""
        link = (fields[2] if len(fields) >= 3 else "").strip().strip("`<>")
        task_id = create_task(chat_id, user.get("id"), name, title, detail, link)
        emoji = get_chat_emoji(chat_id)
        task = fetch_task(task_id)
        kb = keyboard_for_status("open", task_id)
        result = broadcast_task(task, emoji, kb, user.get("id"))
        set_task_message_id(task_id, result.get("message_id"))
        return

    if command == "/tasks":
        if not ensure_bound(chat_id, user.get("id")):
            return
        rows = list_open_tasks(None)
        if not rows:
            send_message(chat_id, "当前没有未完成任务")
            return
        lines = ["未完成任务："]
        for task_id, title, status in rows:
            label = "待接单" if status == "open" else "已接单"
            lines.append(f"- #{task_id} {title or '-'} ({label})")
        send_message(chat_id, "\n".join(lines))
        return


def handle_callback(callback):
    data = callback.get("data", "")
    user = callback.get("from", {})
    name = display_name(user)
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    upsert_user(chat_id, user)

    if ":" not in data:
        answer_callback_query(callback_id, "无效操作")
        return
    action, raw_id = data.split(":", 1)
    if not raw_id.isdigit():
        answer_callback_query(callback_id, "无效任务")
        return
    task_id = int(raw_id)

    if action == "claim":
        if not is_user_bound(chat_id, user.get("id")):
            answer_callback_query(callback_id, "请先绑定 X 账号")
            safe_send_message(user.get("id"), "请先绑定你的 X 账号：\n/bind @your_handle")
            return
        result = claim_task(task_id, user.get("id"), name)
        if result == "duplicate":
            answer_callback_query(callback_id, "你已接单")
            return
        if result != "ok":
            answer_callback_query(callback_id, "任务已完成")
            return
        task = fetch_task(task_id)
        emoji = get_chat_emoji(chat_id)
        text = build_task_text(task, emoji)
        kb = keyboard_for_status("claimed", task_id)
        edit_message(chat_id, message_id, text, reply_markup=kb)
        answer_callback_query(callback_id, "接单成功")
        if task and task.get("creator_id"):
            safe_send_message(task["creator_id"], f"用户 {name} 已接受你的任务 #{task_id}")
        return

    if action == "release":
        ok = release_task(task_id, user.get("id"))
        if not ok:
            answer_callback_query(callback_id, "只有接单者可放弃")
            return
        task = fetch_task(task_id)
        emoji = get_chat_emoji(chat_id)
        text = build_task_text(task, emoji)
        kb = keyboard_for_status("open", task_id)
        edit_message(chat_id, message_id, text, reply_markup=kb)
        answer_callback_query(callback_id, "已放弃")
        return

    if action == "done":
        ok = complete_task(task_id, user.get("id"))
        if not ok:
            answer_callback_query(callback_id, "只有接单者可完成")
            return
        task = fetch_task(task_id)
        emoji = get_chat_emoji(chat_id)
        text = build_task_text(task, emoji)
        edit_message(chat_id, message_id, text)
        answer_callback_query(callback_id, "发布回链（评论或互动链接），将尽快通知车头互动！为你提高曝光度！")
        send_message(chat_id, "请直接在此回复你的回链（评论或互动链接），我会转发给车头。")
        set_user_pending_backlink(chat_id, user.get("id"), task_id)
        return

    answer_callback_query(callback_id, "未知操作")


def poll_updates():
    offset = None
    while True:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = api_call("getUpdates", params)
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            if "message" in update:
                handle_message(update["message"])
            if "callback_query" in update:
                handle_callback(update["callback_query"])
        time.sleep(POLL_INTERVAL)


def main():
    init_db()
    poll_updates()


if __name__ == "__main__":
    main()
