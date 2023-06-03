from email.policy import default
from flask_login import UserMixin
from sqlalchemy import null, event
from app import app, db, login_manager, cache
from sqlalchemy.orm import backref
from werkzeug.security import generate_password_hash, check_password_hash



# user table.
class User(UserMixin, db.Model):

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)
    hashed_password = db.Column(db.String(128))

    # relationship
    user_4nc = db.relationship('User4NC', backref='user')

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
    player1_id = db.Column(db.Integer, nullable=True)
    player2_id = db.Column(db.Integer, nullable=True)
    player3_id = db.Column(db.Integer, nullable=True)
    player4_id = db.Column(db.Integer, nullable=True)
    each_turn_time = db.Column(db.Integer, nullable=False, default=60)
    is_game_started = db.Column(db.Boolean, nullable=False, default=False)
    pause = db.Column(db.Boolean, nullable=False, default=False)

    # relationship
    user_4nc = db.relationship('User4NC', backref='four_nation_chess_room', cascade='all, delete') # when a room is deleted, all corresponding user-room relationship will be deleted for safety.

class User4NC(db.Model):
    # this table is used to store user-room relationship
    __tablename__ = "user_4nc"

    uid=db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, autoincrement=False)
    rid=db.Column(db.Integer, db.ForeignKey('four_nation_chess_room.id'), nullable=False)
    sid=db.Column(db.String(128), nullable=True)



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

# @db.event.listens_for(FourNationChessRoom, 'after_flush')
# def delete_room_after_commit(mapper, connection, target):
#     with app.app_context():
#     # 删除缓存项
#         cache.delete("room/"+str(target.id))

# @db.event.listens_for(User4NC, 'after_flush')
# def delete_user4nc_after_commit(mapper, connection, target):
#     with app.app_context():
#     # 删除缓存项
#         cache.delete("user4nc/"+str(target.uid))

# @db.event.listens_for(User, 'after_flush')
# def delete_user_after_commit(mapper, connection, target):
#     with app.app_context():
#     # 删除缓存项
#         cache.delete("user/"+str(target.id))

@event.listens_for(db.session, "after_commit")
def delete_cache_after_commit(session):
    for obj in session.dirty:  # Track updated objects
        if isinstance(obj, FourNationChessRoom):
            cache.delete("room/" + str(obj.id))
        if isinstance(obj, User4NC):
            cache.delete("user4nc/" + str(obj.id))
        if isinstance(obj, User):
            cache.delete("user/" + str(obj.id))
            
    for obj in session.deleted:  # Track deleted objects
        if isinstance(obj, FourNationChessRoom):
            cache.delete("room/" + str(obj.id))
        if isinstance(obj, User4NC):
            cache.delete("user4nc/" + str(obj.id))
        if isinstance(obj, User):
            cache.delete("user/" + str(obj.id))
    for obj in session.new:  # Track newly inserted objects
        if isinstance(obj, FourNationChessRoom):
            cache.delete("room/" + str(obj.id))
        if isinstance(obj, User4NC):
            cache.delete("user4nc/" + str(obj.id))
        if isinstance(obj, User):
            cache.delete("user/" + str(obj.id))

