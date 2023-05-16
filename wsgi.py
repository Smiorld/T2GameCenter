from app import create_app, socketio, db, login_manager

app = create_app(make_db=True,debug=True)
if __name__ == '__main__':
    socketio.run(app,host='0.0.0.0',port=80)