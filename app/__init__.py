import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_ckeditor import CKEditor
from flask_socketio import SocketIO
from flask_caching import Cache
socketio = SocketIO(async_mode='eventlet')
db= SQLAlchemy()
login_manager = LoginManager()
app=Flask(__name__)
def create_app(make_db=False,debug=False) -> Flask:


    app.debug = debug

    # add ckeditor for rich text fields
    ckeditor = CKEditor(app)

    # suppress SQLAlchemy warning
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config['SECRET_KEY'] = 'lalaowow'

    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "data.db")

    # 加载缓存
    cache_config = {
        "DEBUG": debug,
        "CACHE_TYPE": "simple",
        "CACHE_DEFAULT_TIMEOUT": 300
    }
    app.config.from_mapping(cache_config)
    cache = Cache(app)

    # 注册蓝图
    # 加载大厅
    from .lobby import lobby as lobby_blueprint
    app.register_blueprint(lobby_blueprint)
    # 五子棋加载
    # from .gomoku import gomoku as gomoku_blueprint
    # app.register_blueprint(gomoku_blueprint)

    

    db.init_app(app)
    if make_db:
        with app.app_context():
            from . import models
            db.drop_all()
            db.create_all()
            db.session.commit()


    socketio.init_app(app)
    login_manager.init_app(app)
    return app

from . import models