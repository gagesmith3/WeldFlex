from time import sleep
from fairino import Robot
import time

# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')


def testLsaerWeld():
    # robot.ExtDevLoadUDPDriver()
    # time.sleep(1)

    robot.SetLaserWeldingParam(num = 3, scanSpeed = 2000, scanWidth = 3, peakPower = 1500, dutyCycle = 100, Freq = 1000, io_type=1)
    robot.SetLaserWeldingStartExtDoNum(ctrlModeDONum=1)

    robot.Mode(0)
    time.sleep(1)

    desc_pos1 = [-529.591,-77.247,204.008,-178.889,1.356,-14.668]
    desc_pos2 = [-529.593,282.831,204.007,-178.890,1.357,-14.667]
    desc_safe = [-668.674,50.127,211.698,-176.978,5.333,10.535]
    jointPos1 = [-2.427,-73.309,113.912,-131.693,-91.373,102.241]
    jointPos2 = [-37.804,-66.024,103.325,-128.984,-90.488,66.870]
    jointSafe = [-12.938,-58.855,91.909,-129.146,-90.652,66.633]


    error = robot.MoveL(desc_pos=desc_pos1,joint_pos=jointPos1, tool=0, user=0, vel=100, ovl= 2, acc=100)
    print("MoveL return:", error)
    robot.SetLaserWeldingStartEnd(1, io_type=1, max_waittime=10000)

    error = robot.MoveL(desc_pos=desc_pos2, joint_pos=jointPos2, tool=0, user=0, vel=100, ovl= 2, acc=100)
    print("MoveL return:", error)
    robot.SetLaserWeldingStartEnd(0, io_type=1, max_waittime=10000)

    error = robot.MoveL(desc_pos=desc_safe, joint_pos=jointSafe, tool=0, user=0, vel=100, ovl= 2, acc=100)
    print("MoveL return:", error)

    robot.Mode(1)
    time.sleep(1)

    # 关闭连接
    robot.CloseRPC()
    time.sleep(1)

# 调用测试函数
testLsaerWeld()

