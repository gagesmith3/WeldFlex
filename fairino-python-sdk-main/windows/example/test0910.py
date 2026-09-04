from time import sleep
from fairino import Robot
# A connection is established with the robot controller. A successful connection returns a robot object
# 与机器人控制器建立连接，连接成功返回一个机器人对象
robot = Robot.RPC('192.168.58.2')
import time


def testLasertrackandExitAxis(self):

    startexaxisPos = [0.0, 0.0, 0.0, 0.0]
    seamexaxisPos = [10.0, 0.0, 0.0, 0.0]
    endexaxisPos = [20.0, 0.0, 0.0, 0.0]
    offdese = [0.0] * 6
    seamjointPos = [0.0] * 6
    seamdescPose = [0.0] * 6

    i=0
    while i < 10:
        # 运动到需要寻位的起始点
        startjointPos = [58.830, -92.757, 86.939, -81.135, -90.548, 26.358]
        startdescPose = [-74.319, -312.541, 39.168, 177.512, -1.843, 122.527]
        ret = robot.ExtAxisSyncMoveJ(joint_pos=startjointPos, desc_pos=startdescPose,tool=1,user= 0,vel= 10,acc= 100, ovl=100,exaxis_pos= startexaxisPos,blendT= -1,offset_flag= 0,offset_pos= offdese)
        print(f"{ret}")
        # 沿着-y方向开始寻位
        ret = robot.LaserTrackingSearchStart_xyz(0, 100, 300, 1000, 2)
        robot.LaserTrackingSearchStop()
        tool = 0
        user = 0
        ret, seamjointPos, seamdescPose, tool, user, startexaxisPos = robot.GetLaserSeamPos(0, offdese)
        # 如果寻位成功
        if ret == 0:
            print(
                f"{seamjointPos[0]},{seamjointPos[1]},{seamjointPos[2]},{seamjointPos[3]},{seamjointPos[4]},{seamjointPos[5]},{seamdescPose[0]},{seamdescPose[1]},{seamdescPose[2]},{seamdescPose[3]},{seamdescPose[4]},{seamdescPose[5]}")

            # 机器人和扩展轴同步运动到寻位点
            robot.ExtAxisSyncMoveJ(joint_pos=seamjointPos, desc_pos=seamdescPose,tool=1,user= 0,vel= 100,acc= 100, ovl=100,exaxis_pos= seamexaxisPos,blendT= -1,offset_flag= 0,offset_pos= offdese)

            # 开始沿着寻位点进行激光跟踪并与扩展轴同步运动
            robot.LaserTrackingTrackOnOff(1, 2)
            endjointPos = [67.362, -88.180, 83.000, -81.723, -90.086, 34.885]
            enddescPose = [-32.721, -341.899, 36.602, 177.508, -1.841, 122.519]
            robot.ExtAxisSyncMoveL(joint_pos=endjointPos,desc_pos=enddescPose, tool=1,user= 0,vel= 20,acc= 100, ovl=100,blendR= -1,exaxis_pos= endexaxisPos,offset_pos= offdese)
            # 停止跟踪
            robot.LaserTrackingTrackOnOff(0, 2)
        i = i+1
        print(i)
    robot.CloseRPC()

testLasertrackandExitAxis(robot)