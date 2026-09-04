from time import sleep
import time
from fairino import Robot

# 与机器人控制器建立连接
robot = Robot.RPC('192.168.58.2')


def TestSetTrajectoryJSpeed(self):
    while True:
    # error, version = robot.GetSDKVersion()
    # print(f"SDK version: {version}")
    #
    # rtn,robotModel, webversion, controllerVersion = robot.GetSoftwareVersion()
    # print(f"Getsoftwareversion rtn is: {rtn}")
    # print(f"robotmodel is: {robotModel}, webversion is: {webversion}, controllerVersion is: {controllerVersion}\n")
    # 上传轨迹文件
        rtn = robot.TrajectoryJUpLoad("D://zUP/horse.txt")
        print(f"Upload TrajectoryJ A {rtn}")

        # traj_file_name = "horse.txt"
        traj_file_name = "horse.txt"
        # 加载轨迹文件，参数：文件名，速度百分比，是否循环（1:循环）
        rtn = robot.LoadTrajectoryJ(name=traj_file_name, ovl=100, opt=1)
        print(f"LoadTrajectoryJ {traj_file_name}, rtn is: {rtn}")

        # 获取轨迹起始点位姿
        rtn, traj_start_pose = robot.GetTrajectoryStartPose(name=traj_file_name)
        print(f"GetTrajectoryStartPose is: {rtn}")
        print(
            f"desc_pos:{traj_start_pose[0]},{traj_start_pose[1]},{traj_start_pose[2]},{traj_start_pose[3]},{traj_start_pose[4]},{traj_start_pose[5]}")

        time.sleep(1)

        # 设置基础速度并移动到轨迹起始点
        robot.SetSpeed(50)
        robot.MoveCart(desc_pos=traj_start_pose, tool=0, user=0, vel=100, acc=100, ovl=100, blendT=-1, config=-1)

        # 获取轨迹点数
        rtn, traj_num = robot.GetTrajectoryPointNum()
        print(f"GetTrajectoryStartPose rtn is: {rtn}, traj num is: {traj_num}")

        # 开始执行轨迹运动
        rtn = robot.MoveTrajectoryJ()
        print(f"MoveTrajectoryJ rtn is: {rtn}")

        time.sleep(1)

        # 获取机器人实时状态
        trajspeedMode = 0
        for i in range(4):
            rtn, pkg = robot.GetRobotRealTimeState()
            if pkg.motion_done != 0:
                break

                # 设置轨迹速度为20%
                rtn = robot.SetTrajectoryJSpeed(ovl=20.0, mode=trajspeedMode)
                print(f"SetTrajectoryJSpeed is: {rtn} ")

                time.sleep(1)

                # 设置轨迹速度为80%
                rtn = robot.SetTrajectoryJSpeed(ovl=80.0, mode=trajspeedMode)
                print(f"SetTrajectoryJSpeed is: {rtn} ")

                # 设置轨迹速度为80%
                rtn = robot.SetTrajectoryJSpeed1(ovl=50.0)
                print(f"SetTrajectoryJSpeed1 is: {rtn}")

                time.sleep(1)

                # 设置轨迹速度为50%
                rtn = robot.SetTrajectoryJSpeed3(ovl=20.0, mode=trajspeedMode, status=0)
                print(f"SetTrajectoryJSpeed3 is: {rtn}")

                time.sleep(1)

        robot.StopMotion()

        startjointPos = [-3.120, -75.714, 104.685, -173.933, -86.910, -2.060]
        endjointPos = [15.276, -69.671, 96.313, -170.774, -101.939, 8.612]

        startdescPose = [-595.734, -75.079, 398.718, -124.980, 0.086, 90.593]
        enddescPose = [-595.732, -246.999, 398.730, -124.978, 0.086, 90.592]

        ret = robot.MoveL(desc_pos=startdescPose, joint_pos=startjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL is: {ret} ")
        time.sleep(1)
        ret = robot.MoveL1(desc_pos=enddescPose, joint_pos=endjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL1 is: {ret} ")
        time.sleep(1)
        ret = robot.MoveL2(desc_pos=startdescPose, joint_pos=startjointPos, tool=0, user=0, vel=100, ovl=50, acc=100,
                           status=0)
        print(f"MoveL2 is: {ret} ")
        time.sleep(1)
        ret = robot.MoveL(desc_pos=enddescPose, joint_pos=endjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL is: {ret} ")
        time.sleep(1)
    # 关闭连接
    robot.CloseRPC()
    time.sleep(1)


# 调用测试函数
TestSetTrajectoryJSpeed(robot)