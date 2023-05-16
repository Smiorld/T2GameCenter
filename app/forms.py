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