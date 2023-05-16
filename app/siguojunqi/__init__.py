from flask import Blueprint

gomoku = Blueprint('siguojunqi', __name__)

from . import routes, events