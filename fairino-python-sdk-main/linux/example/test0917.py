import time
from fairino import Robot
# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')
id = 1
toolCoord = [0.0] * 6
extoolCoord = [0.0] * 6
wobjCoord = [0.0] * 6
exAxisCoord = [0.0] * 6
for i in range(100):
    print(f"当前ID为:{id}")
    coordSet0 = [0.0] * 6
    coordSet = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    etcp = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    etool = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    cog = [1.0, 2.0, 3.0]
    if i % 2 == 0:
        robot.SetToolCoord(id, coordSet, 0, 0, 1, 0)
        time.sleep(0.1)
        robot.SetWObjCoord(id, coordSet, 0)
        time.sleep(0.1)
        robot.ExtAxisActiveECoordSys(id, 1, coordSet, 1)
        time.sleep(0.1)
        rtn = robot.SetExToolCoord(id, etcp, etool)
        time.sleep(0.1)
        rtn = robot.SetLoadWeight(id, 1.5)
        time.sleep(0.1)
        rtn = robot.SetLoadCoord(cog[0],cog[1],cog[2],id)
        time.sleep(0.1)
    else:
        robot.SetToolCoord(id, coordSet0, 0, 0, 1, 0)
        time.sleep(0.1)
        robot.SetWObjCoord(id, coordSet0, 0)
        time.sleep(0.1)
        robot.ExtAxisActiveECoordSys(id, 1, coordSet0, 1)
        time.sleep(0.1)
        rtn = robot.SetExToolCoord(id, coordSet0, coordSet0)
        time.sleep(0.1)
        rtn = robot.SetLoadWeight(id, 0)
        time.sleep(0.1)
        rtn = robot.SetLoadCoord(coordSet0[0],coordSet0[1],coordSet0[2] , id)
        time.sleep(0.1)
    rtn, toolCoord = robot.GetCurToolCoord()
    print(f"GetToolCoord {toolCoord[0]},{toolCoord[1]},{toolCoord[2]},{toolCoord[3]},{toolCoord[4]},{toolCoord[5]}")
    rtn, wobjCoord = robot.GetCurWObjCoord()
    print(f"GetWObjCoord {wobjCoord[0]},{wobjCoord[1]},{wobjCoord[2]},{wobjCoord[3]},{wobjCoord[4]},{wobjCoord[5]}")
    rtn, extoolCoord = robot.GetCurExToolCoord()
    print(f"GetExToolCoord {extoolCoord[0]},{extoolCoord[1]},{extoolCoord[2]},{extoolCoord[3]},{extoolCoord[4]},{extoolCoord[5]}")
    rtn, exAxisCoord = robot.GetCurExAxisCoord()
    print(f"GetExAxisCoord {exAxisCoord[0]},{exAxisCoord[1]},{exAxisCoord[2]},{exAxisCoord[3]},{exAxisCoord[4]},{exAxisCoord[5]}")
    weight = 0.0
    getCog = [0.0] * 3
    rtn, weight = robot.GetTargetPayload(0)
    rtn, getCog = robot.GetTargetPayloadCog(0)
    print(f"GetTargetPayload {weight},{getCog[0]},{getCog[1]},{getCog[2]}")

    rtn, toolCoord = robot.GetToolCoordWithID(id)
    print(f"GetToolCoordWithID {id},{toolCoord[0]},{toolCoord[1]},{toolCoord[2]},{toolCoord[3]},{toolCoord[4]},{toolCoord[5]}")
    rtn, wobjCoord = robot.GetWObjCoordWithID(id)
    print(f"GetWObjCoordWithID {id},{wobjCoord[0]},{wobjCoord[1]},{wobjCoord[2]},{wobjCoord[3]},{wobjCoord[4]},{wobjCoord[5]}")
    rtn, extoolCoord = robot.GetExToolCoordWithID(id)
    print(f"GetExToolCoordWithID {id},{extoolCoord[0]},{extoolCoord[1]},{extoolCoord[2]},{extoolCoord[3]},{extoolCoord[4]},{extoolCoord[5]}")
    rtn, exAxisCoord = robot.GetExAxisCoordWithID(id)
    print(f"GetExAxisCoordWithID {id},{exAxisCoord[0]},{exAxisCoord[1]},{exAxisCoord[2]},{exAxisCoord[3]},{exAxisCoord[4]},{exAxisCoord[5]}")
    weight = 0.0
    getCog = [0.0] * 3
    rtn, weight, getCog = robot.GetTargetPayloadWithID(id)
    print(f"GetTargetPayloadWithID {id},{weight},{getCog[0]},{getCog[1]},{getCog[2]}")
    time.sleep(0.5)
    print(f"times {i}")