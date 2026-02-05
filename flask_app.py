from flask import Flask, redirect, render_template, request, url_for, jsonify
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import os
import git
import hmac
import hashlib
import json
from db import db_read, db_write
from auth import login_manager, authenticate, register_user
from blackjack_engine import BlackjackGame, hand_value, create_deck
from werkzeug.security import generate_password_hash, check_password_hash
import random
from flask_login import login_user, logout_user, login_required, current_user
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Load .env variables
load_dotenv()
W_SECRET = os.getenv("W_SECRET")
SECRET_KEY = os.getenv("SECRET_KEY")

# Init flask app
app = Flask(__name__)
app.config["DEBUG"] = True
app.secret_key = SECRET_KEY or "supersecret"

# Init auth
login_manager.init_app(app)
login_manager.login_view = "login"

# DON'T CHANGE
def is_valid_signature(x_hub_signature, data, private_key):
    if not x_hub_signature or not private_key:
        return False
    if "=" not in x_hub_signature:
        return False
    hash_algorithm, github_signature = x_hub_signature.split("=", 1)
    algorithm = hashlib.__dict__.get(hash_algorithm)
    if not algorithm:
        return False
    encoded_key = bytes(private_key, "latin-1")
    mac = hmac.new(encoded_key, msg=data, digestmod=algorithm)
    return hmac.compare_digest(mac.hexdigest(), github_signature)

# DON'T CHANGE
@app.post('/update_server')
def webhook():
    x_hub_signature = request.headers.get('X-Hub-Signature')
    if is_valid_signature(x_hub_signature, request.data, W_SECRET):
        repo = git.Repo('./mysite')
        origin = repo.remotes.origin
        origin.pull()
        return 'Updated PythonAnywhere successfully', 200
    return 'Unathorized', 401

# Auth routes
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        user = authenticate(
            request.form["username"],
            request.form["password"]
        )

        if user:
            login_user(user)
            return redirect(url_for("blackjack"))

        error = "Benutzername oder Passwort ist falsch."

    return render_template(
        "login.html",
        error=error
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        dob_str = request.form.get("date_of_birth", "")
        age_confirm = request.form.get("age_confirm")
        privacy_confirm = request.form.get("privacy_confirm")

        if not username:
            error = "Please enter a username."
        elif not password:
            error = "Please enter a password."
        elif not email:
            error = "Please enter a valid email address."
        elif not age_confirm or not privacy_confirm:
            error = "Please confirm the 18+ notice and Privacy Policy."
        else:
            try:
                dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
                today = date.today()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                if age < 18:
                    error = "You must be 18 or older to register."
            except ValueError:
                error = "Please enter a valid date of birth."

        if error is None:
            ok = register_user(username, email, password)
            if ok:
                return redirect(url_for("login"))

            error = "Username or email already exists."

    return render_template(
        "register.html",
        error=error
    )

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))



# App routes
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Redirect to blackjack"""
    return redirect(url_for("blackjack"))


# Blackjack routes
@app.route("/blackjack", methods=["GET"])
@login_required
def blackjack():
    """Show blackjack page with wallet balance"""
    wallet = db_read("SELECT balance FROM wallets WHERE user_id=%s", (current_user.id,), single=True)
    if not wallet:
        # Create wallet if doesn't exist
        db_write("INSERT INTO wallets (user_id, balance) VALUES (%s, 1000.00)", (current_user.id,))
        balance = 1000.00
    else:
        balance = float(wallet["balance"])
    
    show_tutorial = not bool(getattr(current_user, "tutorial_seen_blackjack", False))
    return render_template("blackjack.html", balance=balance, show_tutorial=show_tutorial)


@app.route("/deposit", methods=["GET", "POST"])
@login_required
def deposit():
    error = None
    success = None

    wallet = db_read("SELECT balance FROM wallets WHERE user_id=%s", (current_user.id,), single=True)
    if not wallet:
        db_write("INSERT INTO wallets (user_id, balance) VALUES (%s, 0.00)", (current_user.id,))
        balance = 0.00
    else:
        balance = float(wallet["balance"])

    if request.method == "POST":
        amount_raw = request.form.get("amount", "0")
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0

        if amount <= 0:
            error = "Please enter a valid amount."
        else:
            new_balance = balance + amount
            db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
            db_write(
                "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                (current_user.id, amount, "deposit", "Demo top-up")
            )
            balance = new_balance
            success = "Funds added successfully (demo)."

    return render_template("deposit.html", balance=balance, error=error, success=success)


@app.route("/settings", methods=["GET"])
@login_required
def settings():
    status = request.args.get("status")
    message = None
    if status == "success":
        message = "Account updated successfully."
    elif status == "error":
        message = "Could not update account. Check your details."
    elif status == "exists":
        message = "Username or email already exists."
    elif status == "password":
        message = "Current password is incorrect."
    return render_template("settings.html", account_status=message)


def _wallet_balance(user_id):
    wallet = db_read("SELECT balance FROM wallets WHERE user_id=%s", (user_id,), single=True)
    if not wallet:
        db_write("INSERT INTO wallets (user_id, balance) VALUES (%s, 0.00)", (user_id,))
        return 0.00
    return float(wallet["balance"])


def _compute_personal_best_balance(user_id, current_balance):
    tx = db_read(
        "SELECT amount FROM transactions WHERE user_id=%s ORDER BY created_at ASC",
        (user_id,),
    )
    total_change = sum(float(t["amount"]) for t in tx)
    starting_balance = current_balance - total_change
    running = starting_balance
    best = starting_balance
    for t in tx:
        running += float(t["amount"])
        if running > best:
            best = running
    return round(best, 2)


def _bonus_xp(user_id):
    row = db_read(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM xp_rewards WHERE user_id=%s",
        (user_id,),
        single=True,
    )
    return int((row or {}).get("total") or 0)


def _award_xp_once(user_id, source, amount):
    if amount <= 0:
        return False
    existing = db_read(
        "SELECT id FROM xp_rewards WHERE user_id=%s AND source=%s LIMIT 1",
        (user_id, source),
        single=True,
    )
    if existing:
        return False
    return db_write(
        "INSERT INTO xp_rewards (user_id, amount, source) VALUES (%s, %s, %s)",
        (user_id, amount, source),
    )


def _xp_and_level(total_games, wins, bonus_xp=0):
    xp = (total_games * 10) + (wins * 50) + bonus_xp
    level = max(1, xp // 500 + 1)
    return xp, int(level)


def _count_total_games_wins(user_id):
    bj_total = db_read(
        "SELECT COUNT(*) AS total FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE",
        (user_id,),
        single=True,
    )
    bj_wins = db_read(
        "SELECT COUNT(*) AS total FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE AND result='player_win'",
        (user_id,),
        single=True,
    )
    ru_total = db_read(
        "SELECT COUNT(*) AS total FROM roulette_sessions WHERE user_id=%s",
        (user_id,),
        single=True,
    )
    ru_wins = db_read(
        "SELECT COUNT(*) AS total FROM roulette_sessions WHERE user_id=%s AND win=TRUE",
        (user_id,),
        single=True,
    )
    total_games = int((bj_total or {}).get("total") or 0) + int((ru_total or {}).get("total") or 0)
    wins = int((bj_wins or {}).get("total") or 0) + int((ru_wins or {}).get("total") or 0)
    return total_games, wins


def _lucky_wheel_segments():
    return [
        {"label_key": "wheel.segment.coins50", "label": "$50", "type": "money", "value": 50, "color": "#f0c061"},
        {"label_key": "wheel.segment.xp100", "label": "XP 100", "type": "xp", "value": 100, "color": "#4fd1c5"},
        {"label_key": "wheel.segment.none", "label": "No win", "type": "none", "value": 0, "color": "#4b5563"},
        {"label_key": "wheel.segment.coins150", "label": "$150", "type": "money", "value": 150, "color": "#d69e2e"},
        {"label_key": "wheel.segment.xp250", "label": "XP 250", "type": "xp", "value": 250, "color": "#38b2ac"},
        {"label_key": "wheel.segment.none", "label": "No win", "type": "none", "value": 0, "color": "#4b5563"},
        {"label_key": "wheel.segment.coins300", "label": "$300", "type": "money", "value": 300, "color": "#f6ad55"},
        {"label_key": "wheel.segment.coins500", "label": "$500", "type": "money", "value": 500, "color": "#ed8936"},
        {"label_key": "wheel.segment.xp500", "label": "XP 500", "type": "xp", "value": 500, "color": "#4299e1"},
    ]


def _rank_title(level):
    if level >= 20:
        return "High Roller"
    if level >= 15:
        return "Pro"
    if level >= 10:
        return "Advanced"
    if level >= 5:
        return "Intermediate"
    return "Beginner"


@app.route("/stats", methods=["GET"])
@login_required
def stats():
    bj_sessions = db_read(
        "SELECT result, created_at, player_hand FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE ORDER BY created_at ASC",
        (current_user.id,),
    )
    roulette_sessions = db_read(
        "SELECT win, created_at FROM roulette_sessions WHERE user_id=%s ORDER BY created_at ASC",
        (current_user.id,),
    )

    bj_total = len(bj_sessions)
    bj_wins = sum(1 for s in bj_sessions if s.get("result") == "player_win")
    bj_losses = sum(1 for s in bj_sessions if s.get("result") in ("dealer_win", "player_bust"))
    bj_pushes = sum(1 for s in bj_sessions if s.get("result") == "push")
    bj_win_rate = round((bj_wins / bj_total) * 100, 1) if bj_total else 0

    ru_total = len(roulette_sessions)
    ru_wins = sum(1 for s in roulette_sessions if s.get("win"))
    ru_losses = ru_total - ru_wins
    ru_win_rate = round((ru_wins / ru_total) * 100, 1) if ru_total else 0

    total_games = bj_total + ru_total
    wins = bj_wins + ru_wins
    losses = bj_losses + ru_losses
    pushes = bj_pushes
    win_rate = round((wins / total_games) * 100, 1) if total_games else 0

    # Achievements
    first_win = wins > 0
    first_blackjack = False
    max_streak = 0
    current_streak = 0

    combined = []
    for s in bj_sessions:
        combined.append({
            "created_at": s.get("created_at"),
            "win": s.get("result") == "player_win",
            "result": s.get("result"),
            "player_hand": s.get("player_hand"),
        })
    for s in roulette_sessions:
        combined.append({
            "created_at": s.get("created_at"),
            "win": bool(s.get("win")),
            "result": "roulette_win" if s.get("win") else "roulette_loss",
            "player_hand": None,
        })
    combined.sort(key=lambda x: x.get("created_at") or datetime.min)

    for s in combined:
        if s.get("win"):
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

        if not first_blackjack and s.get("player_hand"):
            try:
                hand = json.loads(s.get("player_hand") or "[]")
                if len(hand) == 2 and hand_value(hand) == 21:
                    first_blackjack = True
            except Exception:
                pass

    achievements = [
        {
            "id": "first_win",
            "title_key": "stats.achievement.firstWin.title",
            "title": "First Win",
            "unlocked": first_win,
            "desc_key": "stats.achievement.firstWin.desc",
            "desc": "Win your first hand.",
            "xp": 100,
        },
        {
            "id": "first_blackjack",
            "title_key": "stats.achievement.firstBlackjack.title",
            "title": "First Blackjack",
            "unlocked": first_blackjack,
            "desc_key": "stats.achievement.firstBlackjack.desc",
            "desc": "Hit 21 with your first two cards.",
            "xp": 150,
        },
        {
            "id": "win_streak_3",
            "title_key": "stats.achievement.winStreak3.title",
            "title": "3 Win Streak",
            "unlocked": max_streak >= 3,
            "desc_key": "stats.achievement.winStreak3.desc",
            "desc": "Win three hands in a row.",
            "xp": 150,
        },
        {
            "id": "win_streak_5",
            "title_key": "stats.achievement.winStreak5.title",
            "title": "5 Win Streak",
            "unlocked": max_streak >= 5,
            "desc_key": "stats.achievement.winStreak5.desc",
            "desc": "Win five hands in a row.",
            "xp": 250,
        },
        {
            "id": "games_10",
            "title_key": "stats.achievement.games10.title",
            "title": "10 Games Played",
            "unlocked": total_games >= 10,
            "desc_key": "stats.achievement.games10.desc",
            "desc": "Play ten hands.",
            "xp": 100,
        },
    ]

    # Chart data (last 10 sessions)
    recent = combined[-10:]
    chart_points = []
    for s in recent:
        result = s.get("result")
        if result in ("player_win", "roulette_win"):
            value = 1
        elif result == "push":
            value = 0.5
        else:
            value = 0
        label = s.get("created_at").strftime("%b %d") if s.get("created_at") else ""
        chart_points.append({"value": value, "label": label, "result": result})

    def _range_sessions(start, end):
        return [
            s for s in combined
            if s.get("created_at") and start <= s.get("created_at") <= end
        ]

    def _range_max_streak(sessions):
        streak = 0
        max_streak_local = 0
        for s in sessions:
            if s.get("win"):
                streak += 1
                max_streak_local = max(max_streak_local, streak)
            else:
                streak = 0
        return max_streak_local

    def _range_blackjack_hit(sessions):
        for s in sessions:
            if not s.get("player_hand"):
                continue
            try:
                hand = json.loads(s.get("player_hand") or "[]")
                if len(hand) == 2 and hand_value(hand) == 21:
                    return 1
            except Exception:
                continue
        return 0

    def _event_range(start_month, start_day, end_month, end_day, now_time):
        year = now_time.year
        start = datetime(year, start_month, start_day)
        end_year = year + 1 if end_month < start_month else year
        end = datetime(end_year, end_month, end_day, 23, 59)
        if end < now_time:
            year += 1
            start = datetime(year, start_month, start_day)
            end_year = year + 1 if end_month < start_month else year
            end = datetime(end_year, end_month, end_day, 23, 59)
        return start, end

    # Daily challenges
    since = datetime.utcnow() - timedelta(days=1)
    daily_bj = db_read(
        "SELECT result FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE AND created_at >= %s",
        (current_user.id, since),
    )
    daily_roulette = db_read(
        "SELECT win FROM roulette_sessions WHERE user_id=%s AND created_at >= %s",
        (current_user.id, since),
    )
    daily_games = len(daily_bj) + len(daily_roulette)
    daily_wins = sum(1 for s in daily_bj if s.get("result") == "player_win") + sum(1 for s in daily_roulette if s.get("win"))

    challenges = [
        {
            "id": "play5",
            "title_key": "stats.challenge.play5",
            "title": "Play 5 rounds",
            "target": 5,
            "value": daily_games,
            "xp": 50,
        },
        {
            "id": "win2",
            "title_key": "stats.challenge.win2",
            "title": "Win 2 rounds",
            "target": 2,
            "value": daily_wins,
            "xp": 75,
        },
        {
            "id": "play10",
            "title_key": "stats.challenge.play10",
            "title": "Play 10 rounds",
            "target": 10,
            "value": daily_games,
            "xp": 100,
        },
    ]

    for achievement in achievements:
        if achievement.get("unlocked"):
            _award_xp_once(
                current_user.id,
                f"achievement.{achievement['id']}",
                achievement.get("xp", 0),
            )

    daily_key = datetime.utcnow().strftime("%Y-%m-%d")
    for challenge in challenges:
        if challenge.get("value", 0) >= challenge.get("target", 0):
            _award_xp_once(
                current_user.id,
                f"daily.{challenge['id']}.{daily_key}",
                challenge.get("xp", 0),
            )

    bonus_xp = _bonus_xp(current_user.id)
    xp, level = _xp_and_level(total_games, wins, bonus_xp)
    rank_title = _rank_title(level)

    # Event challenges (time-limited)
    now = datetime.utcnow()
    halloween_start, halloween_end = _event_range(10, 28, 10, 31, now)
    winter_start, winter_end = _event_range(12, 20, 12, 26, now)
    new_year_start, new_year_end = _event_range(12, 31, 1, 2, now)

    roulette_black_wins = db_read(
        "SELECT COUNT(*) AS total FROM roulette_sessions "
        "WHERE user_id=%s AND bet_type='color' AND bet_value='black' "
        "AND win=TRUE AND created_at BETWEEN %s AND %s",
        (current_user.id, halloween_start, halloween_end),
        single=True,
    )
    roulette_black_wins = int((roulette_black_wins or {}).get("total") or 0)

    roulette_color_wins = db_read(
        "SELECT COUNT(*) AS total FROM roulette_sessions "
        "WHERE user_id=%s AND bet_type='color' AND win=TRUE "
        "AND created_at BETWEEN %s AND %s",
        (current_user.id, new_year_start, new_year_end),
        single=True,
    )
    roulette_color_wins = int((roulette_color_wins or {}).get("total") or 0)

    halloween_sessions = _range_sessions(halloween_start, halloween_end)
    winter_sessions = _range_sessions(winter_start, winter_end)
    new_year_sessions = _range_sessions(new_year_start, new_year_end)

    halloween_wins = sum(1 for s in halloween_sessions if s.get("win"))
    winter_wins = sum(1 for s in winter_sessions if s.get("win"))

    event_challenges = []
    halloween_challenges = [
        {
            "desc_key": "stats.event.halloween.challenge1",
            "desc": "Win 3 rounds in a row. Bonus: +50% XP during the event.",
            "target": 3,
            "value": _range_max_streak(halloween_sessions),
        },
        {
            "desc_key": "stats.event.halloween.challenge2",
            "desc": "Hit Blackjack once. Bonus: +50% XP during the event.",
            "target": 1,
            "value": _range_blackjack_hit(halloween_sessions),
        },
        {
            "desc_key": "stats.event.halloween.challenge3",
            "desc": "Win on black 2 times (Roulette). Bonus: +50% XP during the event.",
            "target": 2,
            "value": roulette_black_wins,
        },
    ]

    winter_challenges = [
        {
            "desc_key": "stats.event.winter.challenge1",
            "desc": "Play 10 rounds total. Bonus: Daily login reward.",
            "target": 10,
            "value": len(winter_sessions),
        },
        {
            "desc_key": "stats.event.winter.challenge2",
            "desc": "Win 5 times. Bonus: Daily login reward.",
            "target": 5,
            "value": winter_wins,
        },
        {
            "desc_key": "stats.event.winter.challenge3",
            "desc": "Reach a win streak of 3. Bonus: Daily login reward.",
            "target": 3,
            "value": _range_max_streak(winter_sessions),
        },
    ]

    new_year_challenges = [
        {
            "desc_key": "stats.event.newyear.challenge1",
            "desc": "Win on red OR black 3 times (Roulette). Bonus: Double XP on all games.",
            "target": 3,
            "value": roulette_color_wins,
        },
        {
            "desc_key": "stats.event.newyear.challenge2",
            "desc": "Win a hand with Double Down (Blackjack). Bonus: Double XP on all games.",
            "target": 1,
            "value": 0,
        },
        {
            "desc_key": "stats.event.newyear.challenge3",
            "desc": "Reach a new personal best balance. Bonus: Double XP on all games.",
            "target": 1,
            "value": 0,
        },
    ]

    def _append_event(title_key, title, start, end, challenges, theme):
        remaining = max(0, int((end - now).total_seconds()))
        event_challenges.append({
            "title_key": title_key,
            "title": title,
            "theme": theme,
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
            "remaining": remaining,
            "challenges": challenges,
        })

    _append_event(
        "stats.event.halloween.title",
        "Halloween Event – Night of Luck",
        halloween_start,
        halloween_end,
        halloween_challenges,
        "halloween",
    )
    _append_event(
        "stats.event.winter.title",
        "Winter / Christmas Event – Holiday Jackpot",
        winter_start,
        winter_end,
        winter_challenges,
        "winter",
    )
    _append_event(
        "stats.event.newyear.title",
        "New Year Event – Double or Nothing",
        new_year_start,
        new_year_end,
        new_year_challenges,
        "newyear",
    )

    # Personal bests
    current_balance = _wallet_balance(current_user.id)
    best_balance = _compute_personal_best_balance(current_user.id, current_balance)
    most_wins = wins

    # Leaderboards
    users = db_read("SELECT id, username FROM users")
    leaderboard = []
    for u in users:
        uid = u["id"]
        balance = _wallet_balance(uid)
        bj = db_read(
            "SELECT result FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE",
            (uid,),
        )
        ru = db_read(
            "SELECT win FROM roulette_sessions WHERE user_id=%s",
            (uid,),
        )
        total = len(bj) + len(ru)
        win_count = sum(1 for s in bj if s.get("result") == "player_win") + sum(1 for s in ru if s.get("win"))
        loss_count = sum(1 for s in bj if s.get("result") in ("dealer_win", "player_bust")) + sum(1 for s in ru if not s.get("win"))
        bonus_u = _bonus_xp(uid)
        xp_u, level_u = _xp_and_level(total, win_count, bonus_u)
        win_rate_u = round((win_count / total) * 100, 1) if total else 0
        leaderboard.append({
            "username": u["username"],
            "balance": balance,
            "win_rate": win_rate_u,
            "level": level_u,
        })

    top_balance = sorted(leaderboard, key=lambda x: x["balance"], reverse=True)[:5]
    top_win_rate = sorted(leaderboard, key=lambda x: x["win_rate"], reverse=True)[:5]
    top_level = sorted(leaderboard, key=lambda x: x["level"], reverse=True)[:5]

    return render_template(
        "stats.html",
        total_games=total_games,
        wins=wins,
        losses=losses,
        pushes=pushes,
        win_rate=win_rate,
        bj_total=bj_total,
        bj_wins=bj_wins,
        bj_losses=bj_losses,
        bj_pushes=bj_pushes,
        bj_win_rate=bj_win_rate,
        ru_total=ru_total,
        ru_wins=ru_wins,
        ru_losses=ru_losses,
        ru_win_rate=ru_win_rate,
        achievements=achievements,
        chart_points=chart_points,
        xp=xp,
        level=level,
        rank_title=rank_title,
        challenges=challenges,
        event_challenges=event_challenges,
        best_balance=best_balance,
        max_streak=max_streak,
        most_wins=most_wins,
        top_balance=top_balance,
        top_win_rate=top_win_rate,
        top_level=top_level,
    )


@app.route("/help", methods=["GET"])
def help_page():
    return render_template("help.html")


@app.route("/lucky-wheel", methods=["GET"])
@login_required
def lucky_wheel():
    balance = _wallet_balance(current_user.id)
    total_games, wins = _count_total_games_wins(current_user.id)
    bonus_xp = _bonus_xp(current_user.id)
    xp, level = _xp_and_level(total_games, wins, bonus_xp)

    last_free = db_read(
        "SELECT created_at FROM lucky_wheel_spins WHERE user_id=%s AND cost=0 ORDER BY created_at DESC LIMIT 1",
        (current_user.id,),
        single=True,
    )
    now = datetime.utcnow()
    free_available = True
    next_free_seconds = 0
    if last_free and last_free.get("created_at"):
        delta = now - last_free["created_at"]
        remaining = timedelta(days=1) - delta
        next_free_seconds = max(0, int(remaining.total_seconds()))
        free_available = next_free_seconds == 0

    return render_template(
        "lucky_wheel.html",
        balance=balance,
        xp=xp,
        level=level,
        segments=_lucky_wheel_segments(),
        free_available=free_available,
        next_free_seconds=next_free_seconds,
        spin_cost=100,
    )


@app.post("/lucky-wheel/spin")
@login_required
def lucky_wheel_spin():
    segments = _lucky_wheel_segments()
    now = datetime.utcnow()
    balance = _wallet_balance(current_user.id)

    last_free = db_read(
        "SELECT created_at FROM lucky_wheel_spins WHERE user_id=%s AND cost=0 ORDER BY created_at DESC LIMIT 1",
        (current_user.id,),
        single=True,
    )
    free_available = True
    if last_free and last_free.get("created_at"):
        free_available = (now - last_free["created_at"]) >= timedelta(days=1)

    cost = 0 if free_available else 100
    if cost > 0 and balance < cost:
        return jsonify({"ok": False, "error_key": "wheel.errorBalance"}), 400

    if cost > 0:
        balance -= cost
        db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (balance, current_user.id))
        db_write(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
            (current_user.id, -cost, "lucky_wheel_fee", "Lucky Wheel spin fee"),
        )

    segment_index = random.randint(0, len(segments) - 1)
    segment = segments[segment_index]
    reward_type = segment["type"]
    reward_value = int(segment["value"])

    if reward_type == "money" and reward_value > 0:
        balance += reward_value
        db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (balance, current_user.id))
        db_write(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
            (current_user.id, reward_value, "lucky_wheel_reward", "Lucky Wheel reward"),
        )
    elif reward_type == "xp" and reward_value > 0:
        db_write(
            "INSERT INTO xp_rewards (user_id, amount, source) VALUES (%s, %s, %s)",
            (current_user.id, reward_value, "lucky_wheel"),
        )

    db_write(
        "INSERT INTO lucky_wheel_spins (user_id, reward_type, reward_value, cost) VALUES (%s, %s, %s, %s)",
        (current_user.id, reward_type, reward_value, cost),
    )

    last_free_time = now if cost == 0 else (last_free.get("created_at") if last_free else None)
    if last_free_time:
        remaining = timedelta(days=1) - (now - last_free_time)
        next_free_seconds = max(0, int(remaining.total_seconds()))
        free_available = next_free_seconds == 0
    else:
        next_free_seconds = 0
        free_available = True

    total_games, wins = _count_total_games_wins(current_user.id)
    bonus_xp = _bonus_xp(current_user.id)
    xp, level = _xp_and_level(total_games, wins, bonus_xp)

    return jsonify({
        "ok": True,
        "segment_index": segment_index,
        "reward_type": reward_type,
        "reward_value": reward_value,
        "balance": balance,
        "xp": xp,
        "level": level,
        "free_available": free_available,
        "next_free_seconds": next_free_seconds,
    })


@app.route("/roulette", methods=["GET"])
@login_required
def roulette():
    balance = _wallet_balance(current_user.id)
    best_balance = _compute_personal_best_balance(current_user.id, balance)

    bj_sessions = db_read(
        "SELECT result, created_at, player_hand FROM blackjack_sessions WHERE user_id=%s AND finished=TRUE",
        (current_user.id,),
    )
    ru_sessions = db_read(
        "SELECT win, created_at FROM roulette_sessions WHERE user_id=%s",
        (current_user.id,),
    )
    combined = []
    for s in bj_sessions:
        combined.append({
            "created_at": s.get("created_at"),
            "win": s.get("result") == "player_win",
        })
    for s in ru_sessions:
        combined.append({
            "created_at": s.get("created_at"),
            "win": bool(s.get("win")),
        })
    combined.sort(key=lambda x: x.get("created_at") or datetime.min)

    max_streak = 0
    current_streak = 0
    for s in combined:
        if s.get("win"):
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    most_wins = sum(1 for s in combined if s.get("win"))

    show_tutorial = not bool(getattr(current_user, "tutorial_seen_roulette", False))
    return render_template(
        "roulette.html",
        balance=balance,
        best_balance=best_balance,
        max_streak=max_streak,
        most_wins=most_wins,
        show_tutorial=show_tutorial,
    )


@app.post("/account/update")
@login_required
def account_update():
    current_password = request.form.get("current_password", "")
    row = db_read("SELECT id, username, email, password FROM users WHERE id=%s", (current_user.id,), single=True)
    if not row or not check_password_hash(row["password"], current_password):
        return redirect(url_for("settings", status="password"))

    new_username = request.form.get("username", "").strip()
    new_email = request.form.get("email", "").strip().lower()
    new_password = request.form.get("new_password", "").strip()

    if new_username and new_username != row["username"]:
        existing = db_read("SELECT id FROM users WHERE username=%s", (new_username,), single=True)
        if existing:
            return redirect(url_for("settings", status="exists"))
        db_write("UPDATE users SET username=%s WHERE id=%s", (new_username, current_user.id))
        current_user.username = new_username

    if new_email and new_email != (row.get("email") or ""):
        existing = db_read("SELECT id FROM users WHERE email=%s", (new_email,), single=True)
        if existing:
            return redirect(url_for("settings", status="exists"))
        db_write("UPDATE users SET email=%s WHERE id=%s", (new_email, current_user.id))

    if new_password:
        hashed = generate_password_hash(new_password)
        db_write("UPDATE users SET password=%s WHERE id=%s", (hashed, current_user.id))

    return redirect(url_for("settings", status="success"))


@app.post("/tutorial/seen")
@login_required
def tutorial_seen():
    data = request.get_json(silent=True) or {}
    game = data.get("game")
    if game == "blackjack":
        db_write("UPDATE users SET tutorial_seen_blackjack=TRUE WHERE id=%s", (current_user.id,))
    elif game == "roulette":
        db_write("UPDATE users SET tutorial_seen_roulette=TRUE WHERE id=%s", (current_user.id,))
    return jsonify({"ok": True})


@app.post("/roulette/spin")
@login_required
def roulette_spin():
    data = request.get_json(silent=True) or {}
    bets = data.get("bets")

    if not bets:
        # Fallback to legacy single bet
        bet_type = request.form.get("bet_type", "")
        bet_value = request.form.get("bet_value", "")
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        bets = [{"type": bet_type, "value": bet_value, "amount": amount}]

    cleaned = []
    total_bet = 0
    for b in bets:
        b_type = str(b.get("type", "")).strip().lower()
        b_value = str(b.get("value", "")).strip().lower()
        try:
            b_amount = float(b.get("amount", 0))
        except ValueError:
            b_amount = 0
        if b_amount <= 0:
            continue

        valid = False
        if b_type == "number":
            valid = b_value.isdigit() and 0 <= int(b_value) <= 36
        elif b_type == "color":
            valid = b_value in ("red", "black")
        elif b_type == "parity":
            valid = b_value in ("odd", "even")
        elif b_type == "range":
            valid = b_value in ("low", "high")
        elif b_type == "dozen":
            valid = b_value in ("1st", "2nd", "3rd")
        elif b_type == "column":
            valid = b_value in ("1", "2", "3")

        if valid:
            total_bet += b_amount
            cleaned.append({"type": b_type, "value": b_value, "amount": b_amount})

    balance = _wallet_balance(current_user.id)
    if total_bet <= 0:
        return jsonify({"error": "Please place a valid bet."}), 400
    if total_bet > balance:
        return jsonify({"error": "Insufficient balance."}), 400

    result_number = random.randint(0, 36)
    red_numbers = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    result_color = "green" if result_number == 0 else ("red" if result_number in red_numbers else "black")
    result_parity = "even" if result_number != 0 and result_number % 2 == 0 else ("odd" if result_number != 0 else "none")
    result_range = "low" if 1 <= result_number <= 18 else ("high" if 19 <= result_number <= 36 else "none")
    result_dozen = "1st" if 1 <= result_number <= 12 else ("2nd" if 13 <= result_number <= 24 else ("3rd" if 25 <= result_number <= 36 else "none"))
    result_column = "1" if result_number in {1,4,7,10,13,16,19,22,25,28,31,34} else (
        "2" if result_number in {2,5,8,11,14,17,20,23,26,29,32,35} else (
            "3" if result_number in {3,6,9,12,15,18,21,24,27,30,33,36} else "none"
        )
    )

    payout = 0
    for b in cleaned:
        win = False
        multiplier = 0
        if b["type"] == "number":
            win = str(result_number) == b["value"]
            multiplier = 35
        elif b["type"] == "color":
            win = result_color == b["value"]
            multiplier = 1
        elif b["type"] == "parity":
            win = result_parity == b["value"]
            multiplier = 1
        elif b["type"] == "range":
            win = result_range == b["value"]
            multiplier = 1
        elif b["type"] == "dozen":
            win = result_dozen == b["value"]
            multiplier = 2
        elif b["type"] == "column":
            win = result_column == b["value"]
            multiplier = 2

        if win:
            payout += b["amount"] * (multiplier + 1)

    new_balance = balance - total_bet
    db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
    db_write(
        "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
        (current_user.id, -total_bet, "bet", "Roulette bet"),
    )

    if payout > 0:
        new_balance += payout
        db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
        db_write(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
            (current_user.id, payout, "win", "Roulette win"),
        )

    db_write(
        "INSERT INTO roulette_sessions (user_id, bet, bet_type, bet_value, result_number, win, payout) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (current_user.id, total_bet, "multi", "mixed", result_number, payout > 0, payout),
    )

    return jsonify({
        "result_number": result_number,
        "result_color": result_color,
        "result_parity": result_parity,
        "result_range": result_range,
        "result_dozen": result_dozen,
        "result_column": result_column,
        "payout": payout,
        "balance": new_balance,
    })


@app.post("/blackjack/new")
@login_required
def blackjack_new():
    """Start a new blackjack game"""
    bet = float(request.form.get("bet", 10))
    
    # Check wallet balance
    wallet = db_read("SELECT balance FROM wallets WHERE user_id=%s", (current_user.id,), single=True)
    if not wallet or float(wallet["balance"]) < bet:
        return jsonify({"error": "Insufficient balance"}), 400
    
    # Create new game
    game = BlackjackGame()
    
    # Save game session
    db_write(
        "INSERT INTO blackjack_sessions (user_id, bet, player_hand, dealer_hand, finished) VALUES (%s, %s, %s, %s, FALSE)",
        (current_user.id, bet, json.dumps(game.player_hand), json.dumps(game.dealer_hand))
    )
    
    # Get the session ID
    session = db_read(
        "SELECT id FROM blackjack_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 1",
        (current_user.id,),
        single=True
    )
    
    # Deduct bet from wallet
    new_balance = float(wallet["balance"]) - bet
    db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
    
    # Record transaction
    db_write(
        "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
        (current_user.id, -bet, "bet", f"Blackjack bet - Session {session['id']}")
    )
    
    state = game.state()
    state['session_id'] = session['id']
    return jsonify(state)


@app.post("/blackjack/hit")
@login_required
def blackjack_hit():
    """Player hits (takes another card)"""
    session_id = request.form.get("session_id")
    
    # Get current game session
    session = db_read(
        "SELECT * FROM blackjack_sessions WHERE id=%s AND user_id=%s",
        (session_id, current_user.id),
        single=True
    )
    
    if not session:
        return jsonify({"error": "Session not found"}), 404
    
    # Recreate game state
    game = BlackjackGame()
    game.player_hand = json.loads(session["player_hand"])
    game.dealer_hand = json.loads(session["dealer_hand"])
    used_cards = set(game.player_hand + game.dealer_hand)
    remaining = [card for card in create_deck() if card not in used_cards]
    random.shuffle(remaining)
    game.deck = remaining
    game.finished = session["finished"]
    game.result = session["result"]
    
    # Hit
    game.hit()
    
    # Update session
    db_write(
        "UPDATE blackjack_sessions SET player_hand=%s, dealer_hand=%s, finished=%s, result=%s WHERE id=%s",
        (json.dumps(game.player_hand), json.dumps(game.dealer_hand), game.finished, game.result, session_id)
    )
    
    return jsonify(game.state())


@app.post("/blackjack/stand")
@login_required
def blackjack_stand():
    """Player stands (dealer plays)"""
    session_id = request.form.get("session_id")
    
    # Get current game session
    session = db_read(
        "SELECT * FROM blackjack_sessions WHERE id=%s AND user_id=%s",
        (session_id, current_user.id),
        single=True
    )
    
    if not session:
        return jsonify({"error": "Session not found"}), 404
    
    # Recreate game state
    game = BlackjackGame()
    game.player_hand = json.loads(session["player_hand"])
    game.dealer_hand = json.loads(session["dealer_hand"])
    used_cards = set(game.player_hand + game.dealer_hand)
    remaining = [card for card in create_deck() if card not in used_cards]
    random.shuffle(remaining)
    game.deck = remaining
    game.finished = session["finished"]
    game.result = session["result"]
    
    # Stand
    game.stand()
    
    # Calculate payout
    bet = float(session["bet"])
    payout = 0
    
    if game.result == "player_win":
        payout = bet * 2
    elif game.result == "push":
        payout = bet
    
    # Update wallet if player won
    if payout > 0:
        wallet = db_read("SELECT balance FROM wallets WHERE user_id=%s", (current_user.id,), single=True)
        new_balance = float(wallet["balance"]) + payout
        db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
        
        # Record transaction
        db_write(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
            (current_user.id, payout, "win", f"Blackjack win - Session {session_id}")
        )
    
    # Update session
    db_write(
        "UPDATE blackjack_sessions SET player_hand=%s, dealer_hand=%s, finished=%s, result=%s WHERE id=%s",
        (json.dumps(game.player_hand), json.dumps(game.dealer_hand), True, game.result, session_id)
    )
    
    return jsonify(game.state())


if __name__ == "__main__":
    app.run()
