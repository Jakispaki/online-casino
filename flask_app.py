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
from blackjack_engine import BlackjackGame, hand_value
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

# Init flask app
app = Flask(__name__)
app.config["DEBUG"] = True
app.secret_key = "supersecret"

# Init auth
login_manager.init_app(app)
login_manager.login_view = "login"

# DON'T CHANGE
def is_valid_signature(x_hub_signature, data, private_key):
    hash_algorithm, github_signature = x_hub_signature.split('=', 1)
    algorithm = hashlib.__dict__.get(hash_algorithm)
    encoded_key = bytes(private_key, 'latin-1')
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
        password = request.form.get("password", "")
        dob_str = request.form.get("date_of_birth", "")
        age_confirm = request.form.get("age_confirm")
        privacy_confirm = request.form.get("privacy_confirm")

        if not age_confirm or not privacy_confirm:
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
            ok = register_user(username, password)
            if ok:
                return redirect(url_for("login"))

            error = "Username already exists."

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
    
    return render_template("blackjack.html", balance=balance)


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
    return render_template("settings.html")


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


def _xp_and_level(total_games, wins):
    xp = (total_games * 10) + (wins * 50)
    level = max(1, xp // 500 + 1)
    return xp, int(level)


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

    total_games = len(bj_sessions) + len(roulette_sessions)
    wins = sum(1 for s in bj_sessions if s.get("result") == "player_win") + sum(1 for s in roulette_sessions if s.get("win"))
    losses = sum(1 for s in bj_sessions if s.get("result") in ("dealer_win", "player_bust")) + sum(1 for s in roulette_sessions if not s.get("win"))
    pushes = sum(1 for s in bj_sessions if s.get("result") == "push")
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
        {"title": "First Win", "unlocked": first_win, "desc": "Win your first hand."},
        {"title": "First Blackjack", "unlocked": first_blackjack, "desc": "Hit 21 with your first two cards."},
        {"title": "3 Win Streak", "unlocked": max_streak >= 3, "desc": "Win three hands in a row."},
        {"title": "5 Win Streak", "unlocked": max_streak >= 5, "desc": "Win five hands in a row."},
        {"title": "10 Games Played", "unlocked": total_games >= 10, "desc": "Play ten hands."},
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

    xp, level = _xp_and_level(total_games, wins)
    rank_title = _rank_title(level)

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
        {"title": "Play 5 rounds", "target": 5, "value": daily_games},
        {"title": "Win 2 rounds", "target": 2, "value": daily_wins},
        {"title": "Play 10 rounds", "target": 10, "value": daily_games},
    ]

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
        xp_u, level_u = _xp_and_level(total, win_count)
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
        achievements=achievements,
        chart_points=chart_points,
        xp=xp,
        level=level,
        rank_title=rank_title,
        challenges=challenges,
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


@app.route("/roulette", methods=["GET"])
@login_required
def roulette():
    balance = _wallet_balance(current_user.id)
    return render_template("roulette.html", balance=balance)


@app.post("/roulette/spin")
@login_required
def roulette_spin():
    bet_type = request.form.get("bet_type", "")
    bet_value = request.form.get("bet_value", "")
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        amount = 0

    balance = _wallet_balance(current_user.id)
    if amount <= 0:
        return jsonify({"error": "Invalid bet amount."}), 400
    if amount > balance:
        return jsonify({"error": "Insufficient balance."}), 400

    bet_value = bet_value.strip().lower()
    valid = False
    if bet_type == "number":
        valid = bet_value.isdigit() and 0 <= int(bet_value) <= 36
    elif bet_type == "color":
        valid = bet_value in ("red", "black")
    elif bet_type == "parity":
        valid = bet_value in ("odd", "even")

    if not valid:
        return jsonify({"error": "Invalid bet selection."}), 400

    result_number = random.randint(0, 36)
    red_numbers = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    result_color = "green" if result_number == 0 else ("red" if result_number in red_numbers else "black")
    result_parity = "even" if result_number != 0 and result_number % 2 == 0 else ("odd" if result_number != 0 else "none")

    win = False
    multiplier = 0
    if bet_type == "number":
        win = str(result_number) == bet_value
        multiplier = 35
    elif bet_type == "color":
        win = result_color == bet_value
        multiplier = 1
    elif bet_type == "parity":
        win = result_parity == bet_value
        multiplier = 1

    # Deduct bet
    new_balance = balance - amount
    db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
    db_write(
        "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
        (current_user.id, -amount, "bet", "Roulette bet"),
    )

    payout = 0
    if win:
        payout = amount * (multiplier + 1)
        new_balance += payout
        db_write("UPDATE wallets SET balance=%s WHERE user_id=%s", (new_balance, current_user.id))
        db_write(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
            (current_user.id, payout, "win", "Roulette win"),
        )

    db_write(
        "INSERT INTO roulette_sessions (user_id, bet, bet_type, bet_value, result_number, win, payout) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (current_user.id, amount, bet_type, bet_value, result_number, win, payout),
    )

    return jsonify({
        "result_number": result_number,
        "result_color": result_color,
        "result_parity": result_parity,
        "win": win,
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
