# this file should be implemented in room directory instead of lobby. leave it here for now
import eventlet
eventlet.monkey_patch()
from flask import session, request, current_app, copy_current_request_context
from flask_socketio import emit, send, join_room, leave_room, close_room
from .. import socketio, db, app
import ast # for converting string to object and vice versa
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from pytz import utc

# set the scheduler
executors = {
    'default': ThreadPoolExecutor(100),
    'processpool': ProcessPoolExecutor(5)
}
scheduler = BackgroundScheduler(executors=executors, timezone=utc)
scheduler.start()

