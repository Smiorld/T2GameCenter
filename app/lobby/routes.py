from flask import (
    Flask,
    render_template,
    url_for,
    redirect,
    request,
    flash,
    jsonify,
    make_response,
)
from . import lobby
from .. import db
from ..models import User
from flask_login import login_user, logout_user, current_user, login_required
from app.forms import LoginForm, RegistrationForm

@lobby.route("/", methods=["GET", "POST"])
@lobby.route("/index", methods=["GET", "POST"])
@lobby.route("/home", methods=["GET", "POST"])
def home():
    return render_template("home.html" )

@lobby.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("lobby.home"))

@lobby.route("/login", methods=["GET", "POST"])  
def login():
    form = LoginForm()
    if request.method== "POST" and form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.verify_password(form.password.data):
            flash("不存在该账号或密码错误", "warning")
            return render_template("login.html", form=form)
        login_user(user)
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
            flash("注册成功，欢迎您，"+form.username.data+"！")
            new_user = User.query.filter_by(
                    username=form.username.data
                ).first()
            login_user(new_user)
            return redirect(url_for("lobby.home"))
    return render_template("register.html", form=form)

