import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_ckeditor import CKEditor
from flask_socketio import SocketIO
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect
socketio = SocketIO(async_mode='eventlet')
db= SQLAlchemy()
login_manager = LoginManager()
cache = Cache(config={
        "CACHE_TYPE": "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 600
    })
csrf= CSRFProtect()
app=Flask(__name__)
def create_app(make_db=False, delete_room_on_launch=True,debug=False) -> Flask:


    app.debug = debug

    # add ckeditor for rich text fields
    ckeditor = CKEditor(app)

    # suppress SQLAlchemy warning
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config['SECRET_KEY'] = 'IL0veCh1psJustL1keY0u'

    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "data.db")


    # 注册蓝图
    # 加载大厅
    from .lobby import lobby as lobby_blueprint
    app.register_blueprint(lobby_blueprint)
    # 四国军棋加载
    from .fourNationChess import fourNationChess as four_nation_chess_blueprint
    app.register_blueprint(four_nation_chess_blueprint)
    

    db.init_app(app)
    socketio.init_app(app)
    login_manager.init_app(app)
    cache.init_app(app)
    csrf.init_app(app)

    if make_db:
        with app.app_context():
            from . import models
            db.drop_all()
            db.create_all()
            db.session.commit()
    
    if not make_db and delete_room_on_launch:
        with app.app_context():
            from .models import FourNationChessRoom, User4NC
            FourNationChessRoom.query.delete()
            User4NC.query.delete()
            db.session.commit()
            cache.clear()

    return app

from . import models