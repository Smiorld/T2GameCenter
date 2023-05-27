from flask import Blueprint

fourNationChess = Blueprint('fourNationChess', __name__)

from . import routes, events