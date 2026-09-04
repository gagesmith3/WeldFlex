from fairino import Robot
import time
# A connection is established with the robot controller. A successful connection returns a robot object
import time
import threading

# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')


def WeldTraceControlWithCtrlBoxAI(self):
    safetydescPose = [-668.674,50.127,211.698,-176.978,5.333,10.535]
    safetyjointPos = [-12.938,-58.855,91.909,-129.146,-90.652,66.633]

    startdescPose = [-529.591,-77.247,204.008,-178.889,1.356,-14.668]
    startjointPos = [-2.427,-73.309,113.912,-131.693,-91.373,102.241]

    enddescPose = [-529.593,282.831,204.007,-178.890,1.357,-14.667]
    endjointPos = [-37.804,-66.024,103.325,-128.984,-90.488,66.870]

    exaxisPos = [0, 0, 0, 0]
    offdese = [0, 0, 0, 0, 0, 0]
    robot.MoveJ(joint_pos=safetyjointPos, tool=0, user=0, vel=20, acc=100)

    robot.WeldingSetCurrentRelation(0, 495, 1, 10, 0)
    robot.WeldingSetVoltageRelation(10, 45, 1, 10, 1)
    robot.WeldingSetVoltage(0, 25, 1, 0)  # ----设置电压
    robot.WeldingSetCurrent(0, 260, 0, 0)  # ----设置电流

    rtn = robot.ArcWeldTraceAIChannelCurrent(4)
    print("ArcWeldTraceAIChannelCurrent rtn is", rtn)
    rtn = robot.ArcWeldTraceAIChannelVoltage(5)
    print("ArcWeldTraceAIChannelVoltage rtn is", rtn)
    rtn = robot.ArcWeldTraceCurrentPara(0, 5, 0, 500)
    print("ArcWeldTraceCurrentPara rtn is", rtn)
    rtn = robot.ArcWeldTraceVoltagePara(1.018, 10, 0, 50)
    print("ArcWeldTraceVoltagePara rtn is", rtn)

    robot.MoveJ(joint_pos=startjointPos, tool=0, user=0, vel=20, acc=100)
    robot.ArcWeldTraceControl(1, 0, 1, 0.08, 5, 5, 300, 1, 0.06, 4, 4, 300, 1, 0, 4, 1, 10, 0, 0)
    robot.ARCStart(0, 0, 10000)
    robot.WeaveStart(0)
    robot.MoveL(desc_pos=enddescPose, tool=0, user=0, vel=100, ovl= 2, acc=100)
    robot.ARCEnd(0, 0, 10000)
    robot.WeaveEnd(0)
    robot.ArcWeldTraceControl(0, 0, 1, 0.08, 5, 5, 300, 1, 0.06, 4, 4, 300, 1, 0, 4, 1, 10, 0, 0)

    robot.MoveJ(joint_pos=safetyjointPos, tool=0, user=0, vel=20, acc=100)


WeldTraceControlWithCtrlBoxAI(robot)