from time import sleep
from fairino import Robot
from ctypes import sizeof
# A connection is established with the robot controller. A successful connection returns a robot object
# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')

def TestIntersectLineMove(self):
    mainPoint = [[0.0] * 6 for _ in range(6)]
    piecePoint = [[0.0] * 6 for _ in range(6)]

    mainPoint[0] = [-411.572, -516.869, 197.724, -111.821, 31.353, -145.537]
    mainPoint[1] = [-430.242, -575.160, 205.215, -107.763, 47.877, -141.814]
    mainPoint[2] = [-443.560, -608.068, 180.211, -139.983, 78.547, -170.874]
    mainPoint[3] = [-443.718, -608.250, 130.382, -155.397, 80.964, 173.955]
    mainPoint[4] = [-443.718, -608.250, 130.382, -155.397, 80.964, 173.955]
    mainPoint[5] = [-420.815, -527.510, 106.649, 123.128, 67.885, 110.539]

    piecePoint[0] = [-341.600, -568.334, 327.186, 5.404, -3.657, -145.629]
    piecePoint[1] = [-319.224, -619.882, 330.833, 2.439, -3.294, -141.933]
    piecePoint[2] = [-278.636, -609.413, 329.042, 4.194, -7.682, -138.522]
    piecePoint[3] = [-270.948, -567.929, 326.010, 1.932, -4.908, -138.190]
    piecePoint[4] = [-291.152, -544.315, 324.130, -1.220, -5.373, -139.433]
    piecePoint[5] = [-316.419, -543.041, 324.621, 0.387, -5.188, -142.384]

    tool = 2
    wobj = 0
    vel = 100.0
    acc = 100.0
    ovl = 12.0
    oacc = 12.0
    moveType = 0
    moveDirection = 0

    rtn = robot.MoveToIntersectLineStart(mainPoint, piecePoint, tool, wobj, vel, acc, ovl, oacc, moveType)
    print(f"MoveToIntersectLineStart rtn is {rtn}")
    rtn = robot.MoveIntersectLine(mainPoint, piecePoint, tool, wobj, vel, acc, ovl, oacc, moveDirection)
    print(f"MoveIntersectLine rtn is {rtn}")

    robot.CloseRPC()
    return

TestIntersectLineMove(robot)