# this file should be implemented in room directory instead of lobby. leave it here for now
import random
import eventlet
eventlet.monkey_patch()
from flask import session, request, current_app, copy_current_request_context
from flask_socketio import emit, send, join_room, leave_room, close_room
from flask_login import current_user, login_required
from .. import socketio, db, app, cache
from ..models import FourNationChessRoom, User, User4NC
import ast # for converting string to object and vice versa
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from pytz import utc
from datetime import datetime, timedelta
import threading

RECONNECT_TIME = 60 #最大等待重连时间，单位为秒。用于等待用户玩到一半时掉线重连。


# database lock
db_lock = threading.Lock()
cache_lock = threading.Lock()

# set the scheduler
executors = {
    'default': ThreadPoolExecutor(100),
    'processpool': ProcessPoolExecutor(5)
}
scheduler = BackgroundScheduler(executors=executors, timezone=utc)
scheduler.start()

def room_to_dict(room):
    return {
        "id": room.id,
        "is_private": room.is_private,
        "god_perspective": room.god_perspective,
        "password": room.password,
        "player1_id": room.player1_id,
        "player2_id": room.player2_id,
        "player3_id": room.player3_id,
        "player4_id": room.player4_id,
        "is_game_started": room.is_game_started,
        "pause": room.pause,
        "each_turn_time": room.each_turn_time
    }

###### 倒计时函数区
def turn_over_time(room_id): # 回合超时投降函数
    with app.app_context():
        room=FourNationChessRoom.query.filter_by(id=room_id).first()
        if room is not None:
            # TODO:检定谁是当前玩家，然后标记其投降，检定胜负，然后继续下一个玩家的回合
            pass

def reconnect_over_time(room_id): # 重连超时投降函数
    with app.app_context():
        room=FourNationChessRoom.query.filter_by(id=room_id).first()
        if room is not None:
            # TODO:检定是谁没重连，标记他（们）投降，检定胜负，然后继续当前玩家的回合（或正确的下一个玩家）。
            pass

def time_out_date(seconds): # 计算超时时间，里面填入要等待的秒数，返回一个datetime对象用于scheduler计划任务
    return ( datetime.utcnow() + timedelta(seconds=seconds) )

def add_turn_timer(room_id, room): # 添加回合倒计时任务，主要是id=str(room_id)这个参数，用于刷新倒计时任务
    scheduler.remove_job(str(room_id)) # 先移除旧的任务
    scheduler.add_job(turn_over_time, 'date', run_date=time_out_date(room.each_turn_time+5), args=[room_id], id=str(room_id))

def add_reconnect_timer(room_id, room): # 添加重连倒计时任务，主要是id=str(room_id)这个参数，用于刷新倒计时任务
    scheduler.remove_job(str(room_id)) # 先移除旧的任务
    scheduler.add_job(reconnect_over_time, 'date', run_date=time_out_date(RECONNECT_TIME), args=[room_id], id=str(room_id))
###### 倒计时函数区结束

@socketio.on("join", namespace="/4ncRoom")
def on_join(data):
    # 用户的socket链接建立成功，默认添加用户到观众。 data={"room_id":room_id, "password":password}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore

    # 首先确认用户是不是登录了，如果不是，进入观众
    if not current_user.is_authenticated: # type: ignore
        join_room(data['room_id'], sid, namespace="/4ncRoom")
        emit_update_room(data['room_id'])
        return

    # 然后检查用户是否申请进入某个房间。如果没有，拒绝本次请求。
    if data["room_id"] is None:
        emit("room reject", {"message": "请求缺少房间号字段\nMissing parameters for room_id"}, to=sid, namespace="/4ncRoom")
        return

    with db_lock: # 获取数据库锁，防止用户一口气开了多个房间导致多个窗口均通过验证

        room= get_room_by_id(data["room_id"])
        if room is None: # 以防用户手动键入不存在的房间号以进入
            emit("room reject", {"message": "房间不存在\nRoom does not exist."}, to=sid, namespace="/4ncRoom")
            return
        
        # 如果房间是私密的，那么需要密码才能进入
        if room.is_private:
            if data["password"] != room.password:
                emit('room reject', {'message': '密码错误 Password incorrect.'}, to=sid, namespace='/4ncRoom')
                return

        # 密码正确 或 没有密码，允许进入房间。

        
        # 先检定是否处于等待重连的状态，如果是，那么直接将用户的sid更新为当前sid并触发重连成功事件。如果不是 且 用户已在一个房间，那么拒绝本次请求。 
        user = get_user_by_id(uid)
        if user is not None:
            if user.sid is None and room.pause: # 如果用户已经在房间里了，进一步检查用户是否处于断线重连状态

                # 重连。将用户的sid更新为当前sid，后端分配用户进入房间。
                user.sid = sid
                db.session.commit()
                join_room(str(data['room_id'])+'_player', sid, namespace="/4ncRoom")
                # 确认本房间是否还有其他玩家处于等待重连状态，如果没有，那么将房间标记为游戏继续，同时调用继续游戏函数。
                if User4NC.query.filter_by(rid=data['room_id'], sid=None).first() is None:
                    room.pause = False
                    db.session.commit()
                    add_turn_timer(room.id, room) # 添加回合倒计时任务
                    emit_update_room(room.id) # 更新房间信息，游戏继续
                else:
                    # 如果还有其他玩家处于等待重连状态，那么只更新房间信息，等待其他玩家重连
                    emit_update_room(room.id) # 更新房间信息，等待其他玩家重连           
                return
            # 用户不用重连，而是已经再另一个房间了，直接拒绝本次请求
            emit("room reject", {"message": "您已经在其它房间\nYou are already in another room."}, to=sid, namespace="/4ncRoom")
            return
        else:
            # 用户不在其它房间，进入房间成功, 并且将用户信息写入数据库
            user = User4NC(uid=uid, rid=data["room_id"], sid=sid)
            db.session.add(user)
            db.session.commit()

    join_room(data['room_id'], sid, namespace="/4ncRoom")
    emit_update_room(data['room_id'])

@socketio.on("disconnect", namespace="/4ncRoom") # 有socket断开连接
def on_disconnect():
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 看看是不是游客离开了
    if not current_user.is_authenticated: # type: ignore
        # 纯游客离开了，不需要做任何操作
        return
    
    # 需考虑断线重联。如果游戏未开始，那么直接删除该用户在本房间的信息。如果游戏已经开始 且 用户为p1-p4之一，那么需要将战局标记为断线等待，进入额外的断线重连等待时间。
    # 如果用户强行加入了其他房间 或 倒计时未重连，宣布用户离线，且将该用户判定投降。

    ## 我感觉应该需要检查用户信息完整性，但是我的理智告诉我并不需要。我留一个疑惑在这里等待解答。
    with db_lock:
        user4nc = User4NC.query.filter_by(sid=sid).first()
        if user4nc is None: # 奇怪的情况。如果用户不在房间里，那么直接返回，不做任何操作
            return
        
        # 如果用户在房间里，那么检查该局游戏是否开始 且 用户是不是该局游戏玩家
        room = FourNationChessRoom.query.filter_by(id=user4nc.rid).first()
        if room is None: # 奇怪的情况。如果房间不存在，那么直接返回，不做任何操作
            return
        if room.is_game_started and (user4nc.uid == room.player1_id or user4nc.uid == room.player2_id or user4nc.uid == room.player3_id or user4nc.uid == room.player4_id):
            # 如果游戏已经开始且用户是p1-p4之一，那么将房间标记为断线等待，然后移除该玩家的sid作为离线标记
            room.pause = True
            user4nc.sid = None
            db.session.commit()
            # 调用断线重连倒计时函数，同时将旧的sid踢出房间，通知房间内的剩余玩家本事件的发生
            add_reconnect_timer(room.id, room)
            leave_room(user4nc.rid, sid, namespace="/4ncRoom")
            emit_update_room(user4nc.rid)


        else:
            # TODO 否则直接将玩家移除本房间
            pass

@socketio.on("sit down", namespace="/4ncRoom")   
def on_sit_down(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了入座. 先validate能不能入座 data={"room_id":room_id, "player_position":int(1-4)}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore

    # 房间信息
    with db_lock:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)

        if room is None or user4nc is None or room.is_game_started : # TODO 不该发生的事，向用户返回错误 并 打印
            return
        
        # 房间存在且游戏未开始，看看该位置是不是已经入座了
        if (data["player_position"] == 1 and room.player1_id is not None) or (data["player_position"] == 2 and room.player2_id is not None) or (data["player_position"] == 3 and room.player3_id is not None) or (data["player_position"] == 4 and room.player4_id is not None):
            # TODO 位置已经有人了，向用户返回错误
            return
        
        # 位置没人，那么将用户的uid写入房间对应的位置，且在后端将本用户分配到玩家room 而非观众room
        if data["player_position"] == 1:
            room.player1_id = uid
        elif data["player_position"] == 2:
            room.player2_id = uid
        elif data["player_position"] == 3:
            room.player3_id = uid
        elif data["player_position"] == 4:
            room.player4_id = uid
        else:
            # TODO 位置不合法，向用户返回错误
            return
        db.session.commit()
    # 将用户移除观众room 分配到玩家room
    leave_room(data["room_id"], sid, namespace="/4ncRoom")
    join_room(str(data['room_id'])+"_player", sid, namespace="/4ncRoom")

    # 更新房间信息
    emit_update_room(data["room_id"])

@socketio.on("stand up", namespace="/4ncRoom")
def on_stand_up(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了离座. 先validate能不能离座 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    if data["room_id"] is None:
        # TODO 房间不存在，向用户返回错误
        return
    
    with db_lock:
        # 看看用户是不是在房间、是不是正坐着呢
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or room.is_game_started or (user4nc.rid != data["room_id"]): # TODO 不该发生的事，向用户返回错误 并 打印
            return
        
        # 用户在房间且游戏未开始，那么看看用户是不是在座位上，是就优雅起身
        if uid == room.player1_id:
            room.player1_id = None
        elif uid == room.player2_id:
            room.player2_id = None
        elif uid == room.player3_id:
            room.player3_id = None
        elif uid == room.player4_id:
            room.player4_id = None
        else:
            # TODO 用户不在座位上，向用户返回错误
            return
        db.session.commit()
    # 将用户移除玩家room 分配到观众room，更新房间信息，完活
    leave_room(str(data['room_id'])+"_player", sid, namespace="/4ncRoom")
    join_room(data["room_id"], sid, namespace="/4ncRoom")
    emit_update_room(data["room_id"])

@socketio.on("give up", namespace="/4ncRoom")
def on_give_up(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return

def emit_update_room(room_id):
    with app.app_context():

        # get the updated room object
        room = get_room_by_id(room_id)
        if room is None:
            # TODO 房间不存在，向用户返回错误
            return

        # get players info
        player1 = get_user_by_id(room.player1_id) 
        player2 = get_user_by_id(room.player2_id)
        player3 = get_user_by_id(room.player3_id)
        player4 = get_user_by_id(room.player4_id)
       

        # get board data 这里的数据按需存取
        is_lost = cache.get("is_lost/"+str(room_id))
        is_ready = cache.get("is_ready/"+str(room_id))
        current_player = cache.get("current_player/"+str(room_id))
        if is_lost is None:
            # 说明是第一次获取数据，需要初始化
            is_lost = [False, False, False, False]
            cache.set("is_lost/"+str(room_id), is_lost, timeout=0)
        if is_ready is None:
            is_ready = [False, False, False, False]
            cache.set("is_ready/"+str(room_id), is_ready, timeout=0)
        if current_player is None:
            # use current microsecond as seed
            random.seed(datetime.now().microsecond)
            current_player = random.randint(1,4)
            cache.set("current_player/"+str(room_id), current_player, timeout=0)

        # common data 和 specified data 组装. 其中，specified_data_1-4是给四位玩家的数据，而specified_data本体是给观战者的数据。
        common_data = {
            "room": room,
            "player1": player1,
            "player2": player2,
            "player3": player3,
            "player4": player4,
            "is_lost": is_lost,
            "is_ready": is_ready,
            "current_player": current_player,
            "reconnect_time": RECONNECT_TIME
        }
        # TODO 这里的数据按发送的对象来存
        specified_data = {}
        specified_data_1 = {}
        specified_data_2 = {}
        specified_data_3 = {}
        specified_data_4 = {}

        # 接下来将数据分发给房间内的所有人。p1-p4会拿到自己+对家的棋子数据，而观战者会拿到所有棋子数据 或 全盲（取决于god_perspective）。
        if player1 is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_1}, to=player1.user_4nc.sid, namespace="/4ncRoom") 
        if player2 is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_2}, to=player2.user_4nc.sid, namespace="/4ncRoom") 
        if player3 is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_3}, to=player3.user_4nc.sid, namespace="/4ncRoom") 
        if player4 is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_4}, to=player4.user_4nc.sid, namespace="/4ncRoom") 
        emit("update room", {'common_data':common_data,'specified_data':specified_data}, to=room_id, namespace="/4ncRoom")

def intergrity_check(v1, v2):
    # 检查两个uid是否一致，以此确保用户没有伪造uid。目前该函数仅等于检定uid_1 == uid_2，但是以后可以加入更多的检查。
    if v1 != v2 :   
        return False
    else:
        return True
    
def game_start():
    # TODO 四个玩家均准备以后，触发该函数。
    pass



##### 缓存方法区, 用于纯读、纯写操作的简化

def get_room_by_id(room_id) -> FourNationChessRoom | None: 
    with app.app_context():
        # 从缓存中取得房间数据，如果没有，从数据库中取得并写入缓存
        room = cache.get("room/"+str(room_id))
        if room is None:
            room = FourNationChessRoom.query.filter_by(id=room_id).first()
            cache.set("room_"+str(room_id), room)
        return room # type: ignore

def get_user_by_id(user_id) -> User | None:
    with app.app_context():
        # 从缓存中取得用户数据，如果没有，从数据库中取得并写入缓存
        user = cache.get("user/"+str(user_id))
        if user is None:
            user = User.query.filter_by(id=user_id).first()
            cache.set("user/"+str(user_id), user)
        return user # type: ignore

def get_user4nc_by_uid(user_id) -> User4NC | None:
    with app.app_context():
        # 从缓存中取得用户数据，如果没有，从数据库中取得并写入缓存
        user4nc = cache.get("user4nc/"+str(user_id))
        if user4nc is None:
            user4nc = User4NC.query.filter_by(uid=user_id).first()
            cache.set("user4nc/"+str(user_id), user4nc)
        return user4nc # type: ignore

#####