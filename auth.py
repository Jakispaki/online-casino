import logging
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from db import db_read, db_write

# Logger für dieses Modul
logger = logging.getLogger(__name__)

login_manager = LoginManager()


class User(UserMixin):
    def __init__(self, id, username, password, email=None, tutorial_seen=False):
        self.id = id
        self.username = username
        self.password = password
        self.email = email
        self.tutorial_seen = tutorial_seen

    @staticmethod
    def get_by_id(user_id):
        logger.debug("User.get_by_id() aufgerufen mit user_id=%s", user_id)
        try:
            row = db_read(
                "SELECT * FROM users WHERE id = %s",
                (user_id,),
                single=True
            )
            logger.debug("User.get_by_id() DB-Ergebnis: %r", row)
        except Exception:
            logger.exception("Fehler bei User.get_by_id(%s)", user_id)
            return None

        if row:
            return User(row["id"], row["username"], row["password"], row.get("email"), row.get("tutorial_seen"))
        else:
            logger.warning("User.get_by_id(): kein User mit id=%s gefunden", user_id)
            return None

    @staticmethod
    def get_by_username(username):
        logger.debug("User.get_by_username() aufgerufen mit username=%s", username)
        try:
            row = db_read(
                "SELECT * FROM users WHERE username = %s",
                (username,),
                single=True
            )
            logger.debug("User.get_by_username() DB-Ergebnis: %r", row)
        except Exception:
            logger.exception("Fehler bei User.get_by_username(%s)", username)
            return None

        if row:
            return User(row["id"], row["username"], row["password"], row.get("email"), row.get("tutorial_seen"))
        else:
            logger.info("User.get_by_username(): kein User mit username=%s", username)
            return None

    @staticmethod
    def get_by_email(email):
        logger.debug("User.get_by_email() aufgerufen mit email=%s", email)
        try:
            row = db_read(
                "SELECT * FROM users WHERE email = %s",
                (email,),
                single=True
            )
            logger.debug("User.get_by_email() DB-Ergebnis: %r", row)
        except Exception:
            logger.exception("Fehler bei User.get_by_email(%s)", email)
            return None

        if row:
            return User(row["id"], row["username"], row["password"], row.get("email"), row.get("tutorial_seen"))
        else:
            logger.info("User.get_by_email(): kein User mit email=%s", email)
            return None


# Flask-Login
@login_manager.user_loader
def load_user(user_id):
    logger.debug("load_user() aufgerufen mit user_id=%s", user_id)
    try:
        user = User.get_by_id(int(user_id))
    except ValueError:
        logger.error("load_user(): user_id=%r ist keine int", user_id)
        return None

    if user:
        logger.debug("load_user(): User gefunden: %s (id=%s)", user.username, user.id)
    else:
        logger.warning("load_user(): kein User für id=%s gefunden", user_id)

    return user


# Helpers
def register_user(username, email, password):
    logger.info("register_user(): versuche neuen User '%s' anzulegen", username)

    existing = User.get_by_username(username)
    if existing:
        logger.warning("register_user(): Username '%s' existiert bereits", username)
        return False

    existing_email = User.get_by_email(email)
    if existing_email:
        logger.warning("register_user(): Email '%s' existiert bereits", email)
        return False

    hashed = generate_password_hash(password)
    success = db_write(
        "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
        (username, email, hashed)
    )
    
    if success:
        logger.info("register_user(): User '%s' erfolgreich angelegt", username)
        return True
    else:
        logger.error("Fehler beim Anlegen von User '%s'", username)
        return False


def authenticate(username, password):
    logger.info("authenticate(): Login-Versuch für '%s'", username)
    user = User.get_by_email(username) if "@" in username else User.get_by_username(username)

    if not user:
        logger.warning("authenticate(): kein User mit username='%s' gefunden", username)
        return None

    if check_password_hash(user.password, password):
        logger.info("authenticate(): Passwort korrekt für '%s'", username)
        return user
    else:
        logger.warning("authenticate(): falsches Passwort für '%s'", username)
        return None