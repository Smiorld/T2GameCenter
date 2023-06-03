from flask import (
    Flask,
    session,
    render_template,
    url_for,
    redirect,
    request,
    flash,
    jsonify,
    make_response,
)
from . import lobby
from .. import db, cache, login_manager
from ..models import User, FourNationChessRoom
from flask_login import login_user, logout_user, current_user, login_required
from app.forms import LoginForm, RegistrationForm, FourNationChessForm
import threading

lock_4nc = threading.Lock()

@lobby.route("/", methods=["GET", "POST"])
@lobby.route("/index", methods=["GET", "POST"])
@lobby.route("/home", methods=["GET", "POST"])
def home():
    return render_template("home.html" )

@lobby.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("您已成功登出", "success")
    return redirect(url_for("lobby.home"))

@lobby.route("/login", methods=["GET", "POST"])  
def login():
    form = LoginForm()
    if request.method== "POST" and form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.verify_password(form.password.data):
            flash("不存在该账号或密码错误", "warning")
            return render_template("login.html", form=form)
        login_user(user, remember=True) # TODO:记住用户选项实装
        flash("登录成功，"+user.username+"，欢迎回来！")
        return redirect(url_for("lobby.home"))
    else:
        return render_template("login.html", form=form)

@lobby.route("/register", methods=["GET", "POST"])
def register():
    form = RegistrationForm()
    if request.method== "POST":
        if  form.validate_on_submit():
            user = User(username=form.username.data, password=form.password.data) # type: ignore
            db.session.add(user)
            db.session.commit()
            flash("注册成功，欢迎您，"+form.username.data+"！","success")
            new_user = User.query.filter_by(
                    username=form.username.data
                ).first()
            login_user(new_user)
            return redirect(url_for("lobby.home"))
    return render_template("register.html", form=form)


@lobby.route("/4ncLobby", methods=["GET", "POST"])
def FourNationChessLobby():
    form = FourNationChessForm()
    rooms = FourNationChessRoom.query.all()

    return render_template("FourNationChessLobby.html", rooms=rooms,form=form)

@lobby.route("/4ncCreateRoom", methods=["GET", "POST"])
@login_required
def FourNationChessCreateRoom():
    form = FourNationChessForm()
    if request.method== "GET":
        # 返回创建房间页面
        return render_template("4ncCreateRoom.html", form=form)
    else:
        # post method, 直接返回创建后的房间，或者返回错误信息
        with lock_4nc:
            if form.validate_on_submit():
                try:
                    count = FourNationChessRoom.query.count()
                    if count >= 100:
                        flash("房间数量已达上限，请稍后再试。", "warning")
                        return render_template("4ncCreateRoom.html", form=form)
                    room = FourNationChessRoom(is_private=form.is_private.data, god_perspective=form.god_perspective.data, password=form.password.data) # type: ignore
                    if room.is_private:
                        room.password = form.password.data
                    db.session.add(room)
                    db.session.commit()
                    flash("创建成功！您已进入创建好的房间。","success")
                    return redirect(url_for("lobby.FourNationChessGameRoom", room_id=room.id))
                except Exception as e:
                    db.session.rollback()
                    flash("创建失败，请重试。", "warning")
                    return render_template("4ncCreateRoom.html", form=form)
            else:
                flash("验证未通过，创建失败，请重试。", "warning")
                return render_template("4ncCreateRoom.html", form=form)
        
@lobby.route("/4ncRoom/<int:room_id>", methods=["GET", "POST"])
def FourNationChessGameRoom(room_id):
    room = FourNationChessRoom.query.filter_by(id=room_id).first()
    if room is None:
        flash("房间不存在，请从大厅选择存在的房间进入。", "warning")
        return redirect(url_for("lobby.FourNationChessLobby"))
    return render_template("4ncRoom.html", room_id=room_id)





@login_manager.unauthorized_handler
def unauthorized():
    flash("您所请求的页面或操作需要您先登录，请登陆后重试。", "warning")
    return redirect(url_for("lobby.login"))