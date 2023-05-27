from flask_wtf import FlaskForm
from wtforms import (
    DateTimeLocalField,
    SelectField,
    BooleanField,
    StringField,
    PasswordField,
    validators,
    IntegerField,
    SubmitField,
    HiddenField
)
from wtforms.fields import FieldList, FormField
from wtforms.validators import DataRequired, Email, Regexp, EqualTo, ValidationError, Length
from wtforms.widgets import TextArea
from app import db
from app.models import User

class LoginForm(FlaskForm):
    username = StringField(
        "Username", render_kw={"placeholder": "用户名 Username"}, validators=[DataRequired(), Length(1, 20, '用户名长度1-20'),Regexp(r'^[a-zA-Z0-9]+$', message='用户名只可使用英文、数字的组合')]
    )
    password = PasswordField(
        "Password", render_kw={"placeholder": "密码 Password"}, validators=[DataRequired(), Length(6, 20, '密码长度6-20'),Regexp(r'^[a-zA-Z0-9]+$', message='密码只可使用英文、数字的组合')]
    )
    submit = SubmitField("Login")

class RegistrationForm(FlaskForm):
    username = StringField(
        "Username", render_kw={"placeholder": "用户名 Username"}, validators=[DataRequired(), Length(1, 20, '用户名长度1-20'),Regexp(r'^[a-zA-Z0-9]+$', message='用户名只可使用英文、数字的组合')]
    )
    password = PasswordField(
        "Password", render_kw={"placeholder": "密码 Password"}, validators=[DataRequired(), Length(6, 20, '密码长度6-20'),Regexp(r'^[a-zA-Z0-9]+$', message='密码只可使用英文、数字的组合')]
    )
    password2 = PasswordField(
        "Repeat Password", render_kw={"placeholder": "重复密码 Repeat Password"}, validators=[DataRequired(), EqualTo("password", message="两次输入密码不一致")]
    )
    submit = SubmitField("Register")

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError("用户名已存在")
        
class FourNationChessForm(FlaskForm):
    is_private = BooleanField(
        "Private Room", render_kw={"placeholder": "私密房间 Private Room"}, default=False
    )
    password = PasswordField(
        "Password", render_kw={"placeholder": "密码 Password"}
    )
    god_perspective = BooleanField(
        "God Perspective", render_kw={"placeholder": "上帝视角 God Perspective"}, default=False
    )
    turn_time_limit = IntegerField(
        "Turn Time Limit", render_kw={"placeholder": "回合时间限制 Turn Time Limit"}, default=60
    )
    submit = SubmitField("创建")

    def validatge_password(self, password):
        if self.is_private.data and password.data == "":
            raise ValidationError("私密房间必须设置密码")
        elif not self.is_private.data and password.data != "":
            raise ValidationError("公开房间不能设置密码")
