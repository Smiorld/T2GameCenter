# this file should be implemented in room directory instead of lobby. leave it here for now
import random
import eventlet
eventlet.monkey_patch()
from flask import session, request, current_app, copy_current_request_context
from flask_socketio import emit, send, join_room, leave_room, close_room, disconnect
from flask_login import current_user, login_required
from .. import socketio, db, app, cache
from ..models import FourNationChessRoom, User, User4NC
from ..models import cache_lock
import ast # for converting string to object and vice versa
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from pytz import utc
from datetime import datetime, timedelta
import threading

RECONNECT_TIME = 60 #最大等待重连时间，单位为秒。用于等待用户玩到一半时掉线重连。


# locks
db_lock = threading.Lock()
join_room_lock = threading.Lock()
lock_list = []
for _ in range(100):
    lock = threading.Lock()
    lock_list.append(lock)

# set the scheduler
executors = {
    'default': ThreadPoolExecutor(100),
    'processpool': ProcessPoolExecutor(5)
}
scheduler = BackgroundScheduler(executors=executors, timezone=utc)
scheduler.start()


###### 倒计时函数区
def turn_over_time(room_id): # 回合超时投降函数
    with app.app_context():
        # 检定谁是当前玩家，然后标记其投降，检定胜负，然后继续下一个玩家的回合
        current_player = get_current_player(room_id)
        with lock_list[room_id]: #中途再获取锁，因为要确保输的是刚才的超时一瞬间的时候的当前玩家
            player_lose(room_id, current_player)
            who_win = is_game_over(room_id)
            if who_win != 0:
                # TODO:游戏结束，发送结束消息，结束游戏
                game_over(room_id)
                emit_update_room(room_id)
            else:
                # 游戏未结束，继续下一个玩家的回合
                # TODO 发送玩家超时认负信息，当前玩家传递给下一个玩家
                current_player = current_player % 4 + 1
                cache.set("current_player/"+str(room_id), current_player)
                room = get_room_by_id(room_id)
                add_turn_timer(room_id, room) # 添加回合倒计时任务
                emit_update_room(room_id)

def reconnect_over_time(room_id): # 重连超时投降函数
    with app.app_context():
        room= get_room_by_id(room_id)
        if room is not None:
            # 检定是谁没重连，标记他（们）投降，检定胜负，然后继续当前玩家的回合（或正确的下一个玩家）。
            user4nc = User4NC.query.filter_by(rid=room_id, sid=None).all()
            with lock_list[room_id]: #中途再获取锁，因为要确保输的是刚才的超时一瞬间的时候的未连接玩家
                for user in user4nc:
                    player_position = get_player_position(room_id, user.uid)
                    player_lose(room_id, player_position)
                # 判负后，移除这些用户的user4nc信息以说明他们已经离开了房间
                db.session.delete(user4nc)
                db.session.commit()
                who_win = is_game_over(room_id)
                if who_win != 0:
                    # TODO:游戏结束，发送结束消息，结束游戏
                    game_over(room_id)
                    emit_update_room(room_id)
                else:
                    # 游戏未结束，继续当前玩家的回合（或正确的下一个玩家）
                    current_player = get_current_player(room_id)
                    is_lost =  get_is_lost(room_id)
                    if ( current_player == 1 and not is_lost[0] ) or ( current_player == 2 and not is_lost[1] ) or ( current_player == 3 and not is_lost[2] ) or ( current_player == 4 and not is_lost[3] ):
                        # 如果当前玩家没输，那么继续当前玩家的回合
                        cache.set("current_player/"+str(room_id), current_player)
                        room = get_room_by_id(room_id)
                        add_turn_timer(room_id, room)
                        emit_update_room(room_id)
                    else:
                        # 如果当前玩家输了，那么继续下一个未输玩家的回合
                        next_player = current_player % 4 + 1
                        counter=0
                        while is_lost[next_player-1] and counter<4:
                            next_player = next_player % 4 + 1
                            counter+=1
                        if counter==4:
                            #TODO bug了，按程序逻辑不该走到这里
                            app.logger.error("bug: every player loses but game not end")
                            return
                        cache.set("current_player/"+str(room_id), next_player)
                        room = get_room_by_id(room_id)
                        add_turn_timer(room_id, room)
                        emit_update_room(room_id)

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
    uid = current_user.id if current_user.is_authenticated else None # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        emit("room reject", {"message": "房间号字段不合法\nInvalid parameters for room_id"}, to=sid, namespace="/4ncRoom")
        disconnect(sid, namespace="/4ncRoom")
        return
    with join_room_lock and lock_list[data["room_id"]]: # 获取锁，防止用户一口气开了多个房间导致多个窗口均通过验证

        room= get_room_by_id(data["room_id"])
        if room is None: # 以防用户手动键入不存在的房间号以进入
            emit("room reject", {"message": "房间不存在\nRoom does not exist."}, to=sid, namespace="/4ncRoom")
            disconnect(sid, namespace="/4ncRoom")
            return
        
        # 如果房间是私密的，那么需要密码才能进入
        if room.is_private:
            if data["password"] != room.password:
                emit('room reject', {'message': '密码错误 Password incorrect.'}, to=sid, namespace='/4ncRoom')
                disconnect(sid, namespace='/4ncRoom')
                return

        # 密码正确 或 没有密码，允许进入房间。

        
        # 先检定是否处于等待重连的状态，如果是，那么直接将用户的sid更新为当前sid并触发重连成功事件。如果不是 且 用户已在一个房间，那么拒绝本次请求。 
        user4nc = get_user4nc_by_uid(uid)
        if user4nc is not None:
            if user4nc.sid is None and room.pause: # 如果用户已经在房间里了，进一步检查用户是否处于断线重连状态

                # 重连。将用户的sid更新为当前sid，后端分配用户进入房间。
                user4nc.sid = sid
                db.session.commit()
                join_room(str(data['room_id'])+'_player', sid, namespace="/4ncRoom")
                # 确认本房间是否还有其他玩家处于等待重连状态，如果没有，那么将房间标记为游戏继续，同时调用继续游戏函数。
                if User4NC.query.filter_by(rid=data['room_id'], sid=None).first() is None:
                    room.pause = False
                    db.session.commit()
                    add_turn_timer(room.id, room) # 添加回合倒计时任务
                    emit_update_room(room.id) # 更新房间信息，游戏继续
                    return
                else:
                    # 如果还有其他玩家处于等待重连状态，那么只更新房间信息，等待其他玩家重连
                    emit_update_room(room.id) # 更新房间信息，等待其他玩家重连           
                return
            else:
                # 用户不用重连，而是已经再一个房间了，分情况讨论
                if user4nc.rid == data["room_id"] and user4nc.sid != sid:
                    # 如果用户已在本房间，踢出旧的用户，用新的sid建立连接。用于自己顶掉自己的旧连接。
                    emit('room reject', {'message': '您的账号使用了一个新的窗口进入了房间，本窗口的游戏连接已断开；\nYou entered this room with a new window, so this connection has been kicked out.'}, to=user4nc.sid, namespace='/4ncRoom')
                    disconnect(sid=user4nc.sid, namespace='/4ncRoom')
                    # 检测旧用户是玩家还是观众
                    player_position = get_player_position(user4nc.rid, uid)
                    if player_position==0:
                        # 观众
                        leave_room(data["room_id"], user4nc.sid, namespace="/4ncRoom")
                        join_room(data["room_id"], sid, namespace="/4ncRoom")
                    else:
                        # 玩家
                        leave_room(str(data["room_id"])+'_player', user4nc.sid, namespace="/4ncRoom")
                        join_room(str(data["room_id"])+'_player', sid, namespace="/4ncRoom")
                    # 更新用户信息
                    user4nc.sid = sid
                    db.session.commit()
                    emit_update_room(room.id)
                    app.logger.info("用户"+str(uid)+"在房间"+str(data["room_id"])+"中用新的sid"+str(sid)+"连接了游戏")
                    return
                elif user4nc.rid != data["room_id"] :
                    # 如果用户已在其它房间，那么拒绝进入本房间并提示他可以踢掉其它房间的自己。
                    emit('room reject', {'message': '您已经在房间'+str(user4nc.rid)+'中了，您可以先断掉自己的旧连接后再尝试；\nYou are already in room '+str(user4nc.rid)+', you can kick out your old connection in that room.'}, to=sid, namespace='/4ncRoom')
                    disconnect(sid=sid, namespace='/4ncRoom')
                    return
        else:
            # 用户不在其它房间，进入房间成功, 并且将用户信息写入数据库(看看用户是不是游客)
            if current_user.is_authenticated: # type: ignore
                user4nc = User4NC(uid=uid, rid=data["room_id"], sid=sid)
                db.session.add(user4nc)
                db.session.commit()
                app.logger.info('用户'+str(uid)+'进入房间'+str(data['room_id']))
            else:
                # 游客数据不需要进数据库
                app.logger.info('游客进入房间'+str(data['room_id']))

            join_room(data['room_id'], sid, namespace="/4ncRoom")
            emit_update_room(data['room_id'])

@socketio.on("disconnect", namespace="/4ncRoom") # 有socket断开连接
def on_disconnect():
    # 看看是不是游客离开了
    if not current_user.is_authenticated: # type: ignore
        # 纯游客离开了，不需要做任何操作
        app.logger.info('游客离开了4nc房间')
        return
    sid = request.sid # type: ignore
    uid = current_user.id if current_user.is_authenticated else None# type: ignore
    # 需考虑断线重联。如果游戏未开始，那么直接删除该用户在本房间的信息。如果游戏已经开始 且 用户为p1-p4之一，那么需要将战局标记为断线等待，进入额外的断线重连等待时间。
    # 如果用户强行加入了其他房间 或 倒计时未重连，宣布用户离线，且将该用户判定投降。

    ## 我感觉应该需要检查用户信息完整性，但是我的理智告诉我并不需要。我留一个疑惑在这里等待解答。
    user4nc = get_user4nc_by_uid(uid)
    if user4nc is None: # 奇怪的情况。如果用户不在房间里，那么直接返回，不做任何操作
        return
    elif user4nc.sid != sid: # 断开的是一个重复进入了房间的sid，那么直接返回，不做任何操作
        return
    with lock_list[user4nc.rid]:
        # 如果用户在房间里，那么检查该局游戏是否开始 且 用户是不是该局游戏玩家
        room = get_room_by_id(user4nc.rid)
        if room is None: # 奇怪的情况。如果房间不存在，那么直接返回，不做任何操作
            return
        player_position = get_player_position(user4nc.rid, uid)
        if player_position != 0: # 如果用户是玩家1-4之一，那么检查游戏是否已经开始
            is_lost =  get_is_lost(room.id)
            if room.is_game_started and not is_lost[player_position-1] :
                # 如果游戏已经开始且没输，那么将房间标记为断线等待，然后移除该玩家的sid作为离线标记
                room.pause = True
                user4nc.sid = None
                db.session.commit()
                # 调用断线重连倒计时函数，同时将旧的sid踢出房间，通知房间内的剩余玩家本事件的发生
                add_reconnect_timer(room.id, room)
                leave_room(str(room.id)+"_player", sid, namespace="/4ncRoom")
                # 向房间所有人通知该玩家离线，触发客户端进入 重置重连倒计时 状态
                emit("player disconnect", {"player_position": player_position}, to=str(room.id)+"_player", namespace="/4ncRoom")
                emit("player disconnect", {"player_position": player_position}, to=str(room.id), namespace="/4ncRoom")
                emit_update_room(room.id)
                return
            elif room.is_game_started and is_lost[player_position-1] :
                # 如果游戏已经开始且已经输了，那么只是将该玩家移出房间，保留其在room里的信息登记
                leave_room(str(room.id)+"_player", sid, namespace="/4ncRoom")
                db.session.delete(user4nc)
                db.session.commit()
            elif not room.is_game_started :
                # 如果游戏未开始，那么取消用户准备，取消玩家入座，且移出房间
                db.session.delete(user4nc)
                player_cancel_ready(room.id, get_player_position(room.id, uid))
                if player_position == 1:
                    room.player1_id = None
                elif player_position == 2:
                    room.player2_id = None
                elif player_position == 3:
                    room.player3_id = None
                elif player_position == 4:
                    room.player4_id = None
                db.session.commit()
                leave_room(str(room.id)+"_player", sid, namespace="/4ncRoom")
                if is_room_empty(room.id):
                    # 如果房间空了，那么删除房间
                    emit("room delete", {"room_id": room.id}, to=str(room.id), namespace="/4ncRoom")
                    delete_room(room.id)
                    return
                emit_update_room(room.id)
                return
        else:
            # 否则直接将玩家移除本房间
            app.logger.info('用户'+str(uid)+'离开房间'+str(user4nc.rid))
            db.session.delete(user4nc)
            db.session.commit()
            leave_room(room.id, sid, namespace="/4ncRoom")        
            if is_room_empty(room.id):
                # 如果房间空了，那么删除房间
                emit("room delete", {"room_id": room.id}, to=str(room.id), namespace="/4ncRoom")
                delete_room(room.id)
                return
            emit_update_room(room.id)
            return

@socketio.on("sit down", namespace="/4ncRoom")   
def on_sit_down(data):
    app.logger.info('sit down triggered')
    if not current_user.is_authenticated: # type: ignore
        # 游客乱请求，不理他
        app.logger.info('sit down 游客乱请求')
        return
    # 用户点击了入座. 先validate能不能入座 data={"room_id":room_id, "player_position":int(1-4)}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore

    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        #房间号字段不合法，向用户返回错误
        app.logger.info('sit down 房间号字段不合法')
        return
    if not isinstance(data["player_position"], int) or data["player_position"] not in range(1, 5):
        #位置字段不合法，向用户返回错误
        app.logger.info('sit down 位置字段不合法')
        return
    # 房间信息
    with lock_list[data["room_id"]]:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)

        if room is None or user4nc is None or room.is_game_started : # 不该发生的事，向用户返回错误 并 打印
            app.logger.info('sit down 房间不存在 或 用户不在房间 或 游戏已经开始')
            return
        
        # 房间存在且游戏未开始，看看该位置是不是已经入座了
        if (data["player_position"] == 1 and room.player1_id is not None) or (data["player_position"] == 2 and room.player2_id is not None) or (data["player_position"] == 3 and room.player3_id is not None) or (data["player_position"] == 4 and room.player4_id is not None):
            # 位置已经有人了，向用户返回错误
            app.logger.info('sit down 位置已经有人了')
            return
        
        # 位置没人，那么将用户的uid写入房间对应的位置，且在后端将本用户分配到玩家room 而非观众room
        player_position = get_player_position(data["room_id"], uid)
        if player_position != 0:
            # 用户已经在座位上，向用户返回错误
            app.logger.info('sit down 用户已经在座位上')
            return
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
            app.logger.info('sit down 位置参数不合法')
            return
        db.session.commit()
        # 将用户移除观众room 分配到玩家room
        leave_room(data["room_id"], sid, namespace="/4ncRoom")
        join_room(str(data['room_id'])+"_player", sid, namespace="/4ncRoom")

        # 更新房间信息
        emit_update_room(data["room_id"])
        return

@socketio.on("stand up", namespace="/4ncRoom")
def on_stand_up(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了离座. 先validate能不能离座 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        # TODO 房间号字段不合法，向用户返回错误
        return
    with lock_list[data["room_id"]]:
        # 看看用户是不是在房间、是不是正坐着呢
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or room.is_game_started or (user4nc.rid != data["room_id"]): # TODO 不该发生的事，向用户返回错误 并 打印
            return
        
        # 用户在房间且游戏未开始，那么看看用户是不是在座位上，是就优雅起身 + 取消准备
        if uid == room.player1_id:
            room.player1_id = None
            player_cancel_ready(data["room_id"], 1)
        elif uid == room.player2_id:
            room.player2_id = None
            player_cancel_ready(data["room_id"], 2)
        elif uid == room.player3_id:
            room.player3_id = None
            player_cancel_ready(data["room_id"], 3)
        elif uid == room.player4_id:
            room.player4_id = None
            player_cancel_ready(data["room_id"], 4)
        else:
            # TODO 用户不在座位上，向用户返回错误
            return
        db.session.commit()
        # 将用户移除玩家room 分配到观众room，更新房间信息，完活
        leave_room(str(data['room_id'])+"_player", sid, namespace="/4ncRoom")
        join_room(data["room_id"], sid, namespace="/4ncRoom")
        emit_update_room(data["room_id"])
        return

@socketio.on("get ready", namespace="/4ncRoom")
def on_get_ready(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了准备. 先validate能不能准备 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        # TODO 房间号字段不合法，向用户返回错误
        return
    
    with lock_list[data["room_id"]]:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or room.is_game_started or user4nc.rid != data["room_id"]:
             # TODO 不该发生的事，依次为：房间不存在、用户不在任何房间、游戏已经开始、用户在a房间却请求b房间的准备
            return
        # 看看用户是玩家几
        player_position = get_player_position(data["room_id"], uid)
        if player_position != 0:
            # 用户在座位上，那么将用户标记为准备
            player_ready(data["room_id"], player_position)  
        else:
            # TODO 用户不在座位上，向用户返回错误
            return
        # 检测游戏是否就绪
        if is_game_ready(data["room_id"]):
            game_start(data["room_id"])
            add_turn_timer(data["room_id"], room) # 添加回合倒计时任务
        emit_update_room(data["room_id"])

@socketio.on("cancel ready", namespace="/4ncRoom")
def on_cancel_ready(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了取消准备. 先validate能不能取消准备 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        # TODO 房间号字段不合法，向用户返回错误
        return
    with lock_list[data["room_id"]]:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or room.is_game_started or user4nc.rid != data["room_id"]:
             # TODO 不该发生的事，依次为：房间不存在、用户不在任何房间、游戏已经开始、用户在a房间却请求b房间的取消准备
            return
        # 看看用户是玩家几
        player_position = get_player_position(data["room_id"], uid)
        if player_position != 0:
            # 用户在座位上，那么将用户标记为取消准备
            player_cancel_ready(data["room_id"], player_position)  
        else:
            # TODO 用户不在座位上，向用户返回错误
            return
        emit_update_room(data["room_id"])

@socketio.on("give up", namespace="/4ncRoom")
def on_give_up(data):
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了投降. 先validate能不能投降 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        # TODO 房间号字段不合法，向用户返回错误
        return 
    with lock_list[data["room_id"]]:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or not room.is_game_started or room.pause or user4nc.rid != data["room_id"]: 
            # TODO 不该发生的事，依次为：房间不存在、用户不在任何房间、游戏未进行、游戏已暂停、用户在a房间却请求b房间的投降
            # 向用户返回错误 并 打印
            return
        # 看看用户是玩家几
        if uid == room.player1_id:
            player_lose(data["room_id"], 1)
        elif uid == room.player2_id:
            player_lose(data["room_id"], 2)
        elif uid == room.player3_id:
            player_lose(data["room_id"], 3)
        elif uid == room.player4_id:
            player_lose(data["room_id"], 4)
        else:
            # TODO 用户不在座位上，向用户返回错误
            return
        # 有人投降了，刷新一下当前的计时器
        scheduler.reschedule_job(str(data["room_id"]), run_date=time_out_date(room.each_turn_time+5))
        who_win = is_game_over(data["room_id"])
        if who_win != 0:
            # TODO:游戏结束，发送结束消息，结束游戏
            game_over(data["room_id"])
        emit_update_room(data["room_id"])

@socketio.on("move action", namespace="/4ncRoom")
def on_move_action(data):
    # 模拟用户点击了下棋按钮。
    if not current_user.is_authenticated: # type: ignore
        # TODO 用户未登录，向用户返回错误
        return
    # 用户点击了下棋. 先validate能不能下棋 data={"room_id":room_id}
    sid = request.sid # type: ignore
    uid = current_user.id # type: ignore
    # 检查参数合法性
    if not isinstance(data["room_id"], int) or data["room_id"] not in range(1, 101):
        # TODO 房间号字段不合法，向用户返回错误
        return
    with lock_list[data["room_id"]]:
        room = get_room_by_id(data["room_id"])
        user4nc = get_user4nc_by_uid(uid)
        if room is None or user4nc is None or not room.is_game_started or room.pause or user4nc.rid != room.id: 
            # TODO 不该发生的事，依次为：房间不存在、用户不在任何房间、游戏未进行、游戏暂停中、用户在a房间却请求b房间的下棋
            # 向用户返回错误 并 打印
            return
        # 看看用户是玩家几
        player_position = get_player_position(room.id, uid)
        if player_position == 0:
            # TODO 用户不在座位上，向用户返回错误
            return
        # 检查用户是不是当前玩家
        current_player = get_current_player(room.id)
        if player_position != current_player:
            # TODO 用户不是当前玩家，向用户返回错误
            return
        # 行动合法。下一位玩家的回合。
        scheduler.reschedule_job(str(room.id), run_date=time_out_date(room.each_turn_time+5))
        who_win = is_game_over(room.id)
        if who_win != 0:
            # TODO:游戏结束，发送结束消息，结束游戏
            game_over(room.id)
            emit_update_room(room.id)
            return
        # 游戏未结束，继续下一个合法玩家的回合
        current_player = current_player % 4 + 1
        is_lost =  get_is_lost(room.id)
        while is_lost[current_player-1]:
            current_player = current_player % 4 + 1
        cache.set("current_player/"+str(room.id), current_player)
        emit_update_room(room.id)


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
        player1_4nc = get_user4nc_by_uid(room.player1_id)
        player2_4nc = get_user4nc_by_uid(room.player2_id)
        player3_4nc = get_user4nc_by_uid(room.player3_id)
        player4_4nc = get_user4nc_by_uid(room.player4_id)
       

        # get board data 这里的数据按需存取
        is_lost = get_is_lost(room_id)
        is_ready = get_is_ready(room_id)
        current_player = get_current_player(room_id)


        # common data 和 specified data 组装. 其中，specified_data_1-4是给四位玩家的数据，而specified_data本体是给观战者的数据。
        common_data = {
            "room": serialize_room(room),
            "player1": serialize_user(player1),
            "player2": serialize_user(player2),
            "player3": serialize_user(player3),
            "player4": serialize_user(player4),
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
        if player1_4nc is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_1}, to=player1_4nc.sid, namespace="/4ncRoom") 
        if player2_4nc is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_2}, to=player2_4nc.sid, namespace="/4ncRoom") 
        if player3_4nc is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_3}, to=player3_4nc.sid, namespace="/4ncRoom") 
        if player4_4nc is not None:
            emit("update room", {'common_data':common_data,'specified_data':specified_data_4}, to=player4_4nc.sid, namespace="/4ncRoom") 
        emit("update room", {'common_data':common_data,'specified_data':specified_data}, to=room_id, namespace="/4ncRoom")
        return


def is_game_ready(room_id):
    # 检测是否四名玩家均在线并准备了
    with app.app_context():
        is_ready = get_is_ready(room_id)
        if is_ready[0] and is_ready[1] and is_ready[2] and is_ready[3]:
            return True
        else:
            return False
        
def game_start(room_id):
    # 四个玩家均准备以后，触发该函数。
    with app.app_context():
        room = get_room_by_id(room_id)
        if room is None:
            # TODO 房间不存在，向用户返回错误
            return
        if room.is_game_started:
            # TODO 游戏已经开始，向用户返回错误
            return
        # 游戏开始，将房间标记为游戏开始，然后将所有数据正确地修改为游戏开始后的数据。。
        room.is_game_started = True   
        # TODO 各项初始化
        db.session.commit()
        return
        

def player_ready(room_id, player_position):
    # 玩家准备函数。参数为玩家的位置，范围为1-4
    # 该函数仅用于标记玩家准备，将所有数据正确地修改为玩家准备后的数据。不会触发游戏开始。
    with app.app_context():
        if not isinstance(player_position, int) or player_position not in range(1,5):
            # TODO 参数错误，向用户返回错误
            return False
        is_ready = get_is_ready(room_id)
        is_ready[player_position-1] = True
        cache.set("is_ready/"+str(room_id), is_ready, timeout=0)
        return True

def player_cancel_ready(room_id, player_position):
    # 玩家取消准备函数。参数为玩家的位置，范围为1-4
    # 该函数仅用于标记玩家取消准备，将所有数据正确地修改为玩家取消准备后的数据。
    with app.app_context():
        if not isinstance(player_position, int) or player_position not in range(1,5):
            # TODO 参数错误，向用户返回错误
            return False
        is_ready = get_is_ready(room_id)
        is_ready[player_position-1] = False
        cache.set("is_ready/"+str(room_id), is_ready, timeout=0)
        return True

def player_lose(room_id, player_position):
    # 玩家投降或超时或被夺走军旗，触发该函数。参数为玩家的位置，范围为1-4
    # 该函数仅用于标记玩家失败，将所有数据正确地修改为玩家败落后的数据。不会触发游戏结束。
    with app.app_context() and cache_lock:
        if not isinstance(player_position, int) or player_position not in range(1,5):
            # TODO 参数错误，向用户返回错误
            return False
        is_lost = get_is_lost(room_id)
        is_lost[player_position-1] = True
        cache.set("is_lost/"+str(room_id), is_lost, timeout=0)
        return True

def is_game_over(room_id):
    # 检定游戏是否结束，如果结束，返回胜利方的队伍组合，玩家1、3胜利：1，玩家2、4胜利：2，平局：3
    # 该函数仅用于检定游戏是否结束，不会触发游戏结束事件。
    with app.app_context():
        # 完整的检定，不默认任何前提。
        room = get_room_by_id(room_id)
        is_lost = get_is_lost(room_id)
        if room is None or not room.is_game_started:
            # 房间不存在 或 游戏未开始
            return 0
        else:
            # 检定一下有没有某一队输了
            result = 0
            if is_lost[0] and is_lost[2]:
                result+=1
            if is_lost[1] and is_lost[3]:
                result+=2
            return result

def game_over(room_id):
    # 游戏结束，触发该函数。参数为房间号
    # 该函数用于标记游戏结束，将所有数据正确地修改为游戏结束后的数据，并清空本局缓存。
    with app.app_context():
        room = get_room_by_id(room_id)
        if room is None: # 使用前需排除该情况
            return
        # 结束计时器
        scheduler.remove_job(str(room_id))
        # TODO 保存游戏结果

        # 重置参数
        room.is_game_started = False
        room.pause = False
            #清退离场了的玩家
        if room.player1_id is not None:
            user4nc = get_user4nc_by_uid(room.player1_id)
            if user4nc is None:
                room.player1_id = None
        if room.player2_id is not None:
            user4nc = get_user4nc_by_uid(room.player2_id)
            if user4nc is None:
                room.player2_id = None
        if room.player3_id is not None:
            user4nc = get_user4nc_by_uid(room.player3_id)
            if user4nc is None:
                room.player3_id = None
        if room.player4_id is not None:
            user4nc = get_user4nc_by_uid(room.player4_id)
            if user4nc is None:
                room.player4_id = None
        db.session.commit()
        # 清空缓存
        clear_cache(room_id)
        return

def is_room_empty(room_id):
    # 检定房间是否为空，如果为空，返回True，否则返回False
    with app.app_context():
        room_4nc = User4NC.query.filter_by(rid=room_id).all()
        room = get_room_by_id(room_id)
        if room is None:
            # TODO 房间不存在，向用户返回错误
            return True
        if room_4nc is None:
            # TODO 房间不存在，向用户返回错误
            return True
        if len(room_4nc) == 0:
            return True
        else:
            return False

def delete_room(room_id):
    # 删除该room的row + 删除所有仅服务器缓存
    with app.app_context():
        room = get_room_by_id(room_id)
        if room is None:
            # TODO 房间不存在，向用户返回错误
            return False
        db.session.delete(room)
        db.session.commit()
        clear_cache(room_id)
        return True

def get_player_position(room_id, uid):
    with app.app_context():
        room = get_room_by_id(room_id)
        if room is None:
            # TODO 房间不存在，向用户返回错误
            return 0
        if uid == room.player1_id:
            return 1
        elif uid == room.player2_id:
            return 2
        elif uid == room.player3_id:
            return 3
        elif uid == room.player4_id:
            return 4
        else:
            # 用户不在座位上，向用户返回0
            return 0



##### 缓存方法区, 用于纯读操作的简化

def get_room_by_id(room_id) -> FourNationChessRoom | None: 
    with app.app_context() and cache_lock:
        # 从缓存中取得房间数据，如果没有，从数据库中取得并写入缓存
        room = cache.get("room/"+str(room_id))
        if room is None:
            room = FourNationChessRoom.query.filter_by(id=room_id).first()
            cache.set("room/"+str(room_id), room)
        else:
            db.session.add(room)
        return room # type: ignore

def get_user_by_id(user_id) -> User | None:
    with app.app_context() and cache_lock:
        # 从缓存中取得用户数据，如果没有，从数据库中取得并写入缓存
        user = cache.get("user/"+str(user_id))
        if user is None:
            user = User.query.filter_by(id=user_id).first()
            cache.set("user/"+str(user_id), user)
        else:
            db.session.add(user)
        return user # type: ignore

def get_user4nc_by_uid(user_id) -> User4NC | None:
    with app.app_context() and cache_lock:
        # 从缓存中取得用户数据，如果没有，从数据库中取得并写入缓存
        user4nc = cache.get("user4nc/"+str(user_id))
        if user4nc is None:
            user4nc = User4NC.query.filter_by(uid=user_id).first()
            cache.set("user4nc/"+str(user_id), user4nc)
        else:
            db.session.add(user4nc)
        return user4nc # type: ignore
    
def get_is_lost(room_id) -> list[int] :
    with app.app_context() and cache_lock:
        # 从缓存中取得用户数据，如果没有，初始化并写入缓存
        is_lost = cache.get("is_lost/"+str(room_id))
        if is_lost is None:
            is_lost = [False, False, False, False]
            cache.set("is_lost/"+str(room_id), is_lost, timeout=0)
        return is_lost # type: ignore

def get_is_ready(room_id) -> list[int] :
    with app.app_context() and cache_lock:
        # 从缓存中取得用户数据，如果没有，初始化并写入缓存
        is_ready = cache.get("is_ready/"+str(room_id))
        if is_ready is None:
            is_ready = [False, False, False, False]
            cache.set("is_ready/"+str(room_id), is_ready, timeout=0)
        return is_ready # type: ignore

def get_current_player(room_id) -> int :
    with app.app_context() and cache_lock:
        # 从缓存中取得用户数据，如果没有，初始化并写入缓存
        current_player = cache.get("current_player/"+str(room_id))
        if current_player is None:
            # use current microsecond as seed
            random.seed(datetime.now().microsecond)
            current_player = random.randint(1,4)
            cache.set("current_player/"+str(room_id), current_player, timeout=0)
        return current_player # type: ignore

def clear_cache(room_id) -> bool:
    with app.app_context() and cache_lock:
        # 清空仅缓存的数据
        cache.delete("is_lost/"+str(room_id))
        cache.delete("is_ready/"+str(room_id))
        cache.delete("current_player/"+str(room_id))
        return True
#####

##### models.py 所有对象的json serializable方法

def serialize_user(user):
    if user is None:
        return None
    else:
        return {
        'id': user.id,
        'username': user.username
        }

def serialize_room(room):
    if room is None:
        return None
    else:
        return{
            'id': room.id,
            'is_private': room.is_private,
            'god_perspective': room.god_perspective,
            'password': room.password,
            'player1_id': room.player1_id,
            'player2_id': room.player2_id,
            'player3_id': room.player3_id,
            'player4_id': room.player4_id,
            'each_turn_time': room.each_turn_time,
            'is_game_started': room.is_game_started,
            'pause': room.pause
        }

def serialize_user4nc(user4nc):
    if user4nc is None:
        return None
    else:
        return{
            'uid': user4nc.uid,
            'rid': user4nc.rid,
            'sid': user4nc.sid
        }

#####