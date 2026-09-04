
from time import sleep
import time
from fairino import Robot

# 与机器人控制器建立连接
robot = Robot.RPC('192.168.58.2')

def ProgramLoad_test(self):
    # i = 1
    # for i in range (31):
    #     print(f"SDK启动主程序功能测试，第 {i} 次")
        error = robot.ProgramLoad(program_name="test.lua")
        print("ProgramLoad return ", error)

        error, loadednamestr = robot.GetLoadedProgram()
        print("GetLoadedProgram return ", error)
        print("Loaded lua name is : ", loadednamestr)

        robot.Mode(0)
        time.sleep(1)
        robot.ProgramRun()
        time.sleep(1)
        robot.Mode(1)

def LoadDefaultProgConfig_test(self):
    robot.LoadDefaultProgConfig(flag=1, program_name="test.lua")
    error, loadednamestr = robot.GetLoadedProgram()
    print("Loaded lua name is : ", loadednamestr)
    print("GetLoadedProgram return ", error)
    robot.Mode(0)
    time.sleep(100)
    robot.Mode(1)
    # i = 0
    # for i in range(30):
    #     print(f"默认程序配置功能测试，第 {i} 次")
    #     robot.LoadDefaultProgConfig(flag=1, program_name="/fruser/test.lua")
    #     error, loadednamestr = robot.GetLoadedProgram()
    #     print("Loaded lua name is : ", loadednamestr)
    #     print("GetLoadedProgram return ", error)
    #     time.sleep(1)
    #     robot.LoadDefaultProgConfig(flag=1, program_name="test.lua")
    #     error, loadednamestr = robot.GetLoadedProgram()
    #     print("Loaded lua name is : ", loadednamestr)
    #     print("GetLoadedProgram return ", error)

def Traj_test(self):
    i = 0
    for i in range(30):
        print(f"轨迹加载功能测试，第 {i} 次")
        # Upload trajectory file
        rtn = robot.TrajectoryJUpLoad("D://zUP/horse.txt")
        print(f"Upload TrajectoryJ A {rtn}")

        traj_file_name1 = "horse.txt"
        # 加载轨迹文件，参数：文件名，速度百分比，是否循环（1:循环）
        rtn = robot.LoadTrajectoryJ(name=traj_file_name1, ovl=100, opt=1)
        print(f"LoadTrajectoryJ {traj_file_name1}, rtn is: {rtn}")

        # 获取轨迹起始点位姿
        rtn, traj_start_pose = robot.GetTrajectoryStartPose(name=traj_file_name1)
        print(f"GetTrajectoryStartPose is: {rtn}")
        print(
            f"desc_pos:{traj_start_pose[0]},{traj_start_pose[1]},{traj_start_pose[2]},{traj_start_pose[3]},{traj_start_pose[4]},{traj_start_pose[5]}")

        time.sleep(1)

        rtn = robot.TrajectoryJUpLoad("D://zUP/fivestart.txt")
        print(f"Upload TrajectoryJ A {rtn}")

        # Load trajectory with LA parameters
        traj_file_name2 = "fivestart.txt"
        rtn = robot.LoadTrajectoryLA(traj_file_name2, 2, 0, 0, 1, 40, 100, 100, 1)
        print(f"LoadTrajectoryLA {traj_file_name2}, rtn is: {rtn}")

        # Get starting pose
        traj_start_pose = [0.0] * 6  # Initialize as list for x,y,z,rx,ry,rz
        rtn, traj_start_pose = robot.GetTrajectoryStartPose(traj_file_name2)
        print(f"GetTrajectoryStartPose is: {rtn}")
        print(f"desc_pos:{traj_start_pose[0]},{traj_start_pose[1]},{traj_start_pose[2]},"
              f"{traj_start_pose[3]},{traj_start_pose[4]},{traj_start_pose[5]}")

        time.sleep(1)

# ProgramLoad_test(robot)
LoadDefaultProgConfig_test(robot)
#Traj_test(robot)