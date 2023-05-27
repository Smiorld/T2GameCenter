from email.policy import default
from flask_login import UserMixin
from sqlalchemy import null
from app import db, login_manager
from sqlalchemy.orm import backref
from werkzeug.security import generate_password_hash, check_password_hash



# user table.
class User(UserMixin, db.Model):

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)
    hashed_password = db.Column(db.String(128))


    @property
    def password(self):
        raise AttributeError("Password is not readable.")

    @password.setter
    def password(self, password):
        self.hashed_password = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.hashed_password, password)

# 四国军棋 room table
class FourNationChessRoom(db.Model):

    __tablename__ = "four_nation_chess_room"

    id = db.Column(db.Integer, primary_key=True)
    is_private = db.Column(db.Boolean, nullable=False, default=False)
    god_perspective = db.Column(db.Boolean, nullable=False, default=False)
    password = db.Column(db.Integer, nullable=True)
    player1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    player2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    player3_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    player4_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_game_started = db.Column(db.Boolean, nullable=False, default=False)

class FourNationChessHistory(db.Model):

    __tablename__ = "four_nation_chess_history"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False)
    lobby_id = db.Column(db.Integer, nullable=False)
    player1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player3_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player4_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    winner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    match_histroy = db.Column(db.String(1000), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
