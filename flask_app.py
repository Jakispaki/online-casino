from flask import Flask, redirect, render_template, request, url_for, jsonify
from datetime import datetime, date
from dotenv import load_dotenv
import os
import git
import hmac
import hashlib
import json
from db import db_read, db_write
from auth import login_manager, authenticate, register_user
from blackjack_engine import BlackjackGame
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


@app.route("/settings", methods=["GET"])
@login_required
def settings():
    return render_template("settings.html")


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
