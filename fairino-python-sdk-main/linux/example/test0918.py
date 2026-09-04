from time import sleep
from fairino import Robot
import fairino
from ctypes import sizeof
# A connection is established with the robot controller. A successful connection returns a robot object
# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')
import time


def TestCustomWeaveSetPara(self):
    point = [0.0] * 30
    point[0] = -3.0
    point[1] = -3.0
    point[2] = 0.0

    point[3] = -6.0
    point[4] = 0.0
    point[5] = 0.0

    point[6] = -3.0
    point[7] = 3.0
    point[8] = 0.0

    point[9] = 0.0
    point[10] = 0.0
    point[11] = 0.0

    stayTime = [0.0] * 10
    rtn = robot.CustomWeaveSetPara(2, 4, point, stayTime, 1.000, 0, 0)
    print(f"CustomWeaveSetPara rtn is {rtn}")
    time.sleep(1)

    pointNum = 0
    frequency = 0.0
    incStayType = 0
    stationary = 0
    rtn, pointNum, point, stayTime, frequency, incStayType, stationary = robot.CustomWeaveGetPara(2)
    print(f"pointNum is {pointNum}")
    for i in range(pointNum):
        print(f"point {i}, point x y z {point[i * 3 + 0]},{point[i * 3 + 1]},{point[i * 3 + 2]}")
    print(f"fre is {frequency}, stay is {incStayType},{stationary}")

    robot.WeaveSetPara(0, 9, 1.000000, 1, 5.000000, 6.000000, 5.000000, 50, 100, 100, 0, 1, 0.000000, 0.000000)

    desc_p1 = [-668.674,50.127,211.698,-176.978,5.333,10.535]
    desc_p2 = [-529.591,-77.247,204.008,-178.889,1.356,-14.668]
    desc_p3 = [-529.593,282.831,204.007,-178.890,1.357,-14.667]
    j1 = [-12.938,-58.855,91.909,-129.146,-90.652,66.633]
    j2 = [-2.427,-73.309,113.912,-131.693,-91.373,102.241]
    j3 = [-37.804,-66.024,103.325,-128.984,-90.488,66.870]

    epos = [0.0] * 4
    offset_pos = [0.0] * 6

    robot.MoveJ(joint_pos=j1, tool=0, user=0, vel=100)
    robot.WeaveStart(0)
    robot.Circle(desc_pos_p=desc_p3, tool_p=0, user_p=0, vel_p=50, desc_pos_t=desc_p2, tool_t=0, user_t=0, vel_t=50, oacc=10)
    robot.WeaveEnd(0)
    robot.MoveJ(joint_pos=j1, tool=0, user=0, vel=100)
    robot.WeaveStart(0)
    robot.MoveC(desc_pos_p=desc_p3, tool_p=0, user_p=0, vel_p=50, desc_pos_t=desc_p2, tool_t=0, user_t=0, vel_t=50)
    robot.WeaveEnd(0)
    robot.MoveJ(joint_pos=j1, tool=0, user=0, vel=100)
    robot.WeaveStart(0)
    robot.MoveL(desc_pos=desc_p2, tool=0, user=0, vel=100, ovl=10, speedPercent=100)
    robot.WeaveEnd(0)

    robot.CloseRPC()

TestCustomWeaveSetPara(robot)