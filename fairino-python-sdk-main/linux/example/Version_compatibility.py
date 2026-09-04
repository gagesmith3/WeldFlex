from time import sleep
import time
from fairino import Robot

# 与机器人控制器建立连接
robot = Robot.RPC('192.168.58.2')

def mode_test(self):
    #one param
    robot.Mode(1)
    time.sleep(2)
    robot.Mode(0)
    time.sleep(2)
    robot.Mode(1)

    # two params
    # robot.Mode(1,1)
    # time.sleep(2)
    # robot.Mode(0,1)
    # time.sleep(2)
    # robot.Mode(1,1)

    # three params
    # robot.Mode(1,1,1)
    # time.sleep(2)
    # robot.Mode(0,1,1)
    # time.sleep(2)
    # robot.Mode(1,1,1)

def moveL_test(self):

    startjointPos = [-3.120,-75.714,104.685,-173.933,-86.910,-2.060]
    endjointPos = [15.276,-69.671,96.313,-170.774,-101.939,8.612]

    startdescPose = [-595.734,-75.079,398.718,-124.980,0.086,90.593]
    enddescPose = [-595.732,-246.999,398.730,-124.978,0.086,90.592]

    while True:
        ret = robot.MoveL(desc_pos=startdescPose, joint_pos=startjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL is: {ret} ")
        ret = robot.MoveL1(desc_pos=enddescPose, joint_pos=endjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL1 is: {ret} ")
        time.sleep(1)
        ret = robot.MoveL2(desc_pos=startdescPose, joint_pos=startjointPos, tool=0, user=0, vel=100, ovl=50, acc=100, status=0)
        print(f"MoveL2 is: {ret} ")
        ret = robot.MoveL(desc_pos=enddescPose, joint_pos=endjointPos, tool=0, user=0, vel=100, ovl=50, acc=100)
        print(f"MoveL is: {ret} ")
def SetTrajectoryJSpeed_test(self):
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
    trajspeedMode = 0
    while True:
        rtn, pkg = robot.GetRobotRealTimeState()
        if pkg.motion_done != 0:
            print(f"rtn is: {rtn},motion_done is: {pkg.motion_done}")
            break

        # 设置轨迹速度为10%
        rtn = robot.SetTrajectoryJSpeed(ovl=10.0, mode=trajspeedMode)
        print(f"SetTrajectoryJSpeed is: {rtn} ")

        time.sleep(3)

        # 设置轨迹速度为80%
        rtn = robot.SetTrajectoryJSpeed1(ovl=80.0)
        print(f"SetTrajectoryJSpeed1 is: {rtn}")

        time.sleep(3)

        # 设置轨迹速度为50%
        rtn = robot.SetTrajectoryJSpeed3(ovl=50.0, mode=trajspeedMode,status=0)
        print(f"SetTrajectoryJSpeed3 is: {rtn}")

        time.sleep(3)

def TestOriginPointWeave(self):
    time.sleep(2)
    # 初始化关节位置、外部轴和偏移
    j = [39.886, -98.580, -124.032, -47.393, 90.000, 40.842]
    epos = [0, 0, 0, 0]
    offset_pos = [0, 0, 0, 0, 0, 0]

    # 参考点位置 [x, y, z, rx, ry, rz]
    refPoint = [400.021, 300.022, 299.996, 179.997, -0.003, -90.956]

    # 移动到起始位置
    robot.MoveJ(joint_pos=j, tool=1, user=0, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

    # 第一次摆动：绝对坐标系（tool=0），模式0
    robot.OriginPointWeaveStart(0, 0, refPoint, 3)
    robot.MoveStationary()
    robot.OriginPointWeaveEnd()

    time.sleep(2)

    # 再次移动到起始位置
    robot.MoveJ(joint_pos=j, tool=1, user=0, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

    # 第二次摆动：绝对坐标系（tool=0），模式1
    robot.OriginPointWeaveStart(0, 1, refPoint, 3)
    robot.MoveStationary()
    robot.OriginPointWeaveEnd()

    # 关闭连接
    robot.CloseRPC()
    time.sleep(1)

def SetCoderCompenParams_test(self):
    status = 0
    torqueCoeff = [0.2,0.3,0.2,0.3,0.2,0.3]
    rtn = robot.SetCoderCompenParams(status,torqueCoeff)
    print(f"SetTrajectoryJSpeed is: {rtn}")

    # 关闭连接
    robot.CloseRPC()
    time.sleep(1)

def ExtAxisSync_test(self):
    # 关节位置定义
    joint_safe = [33.513, -89.540, -19.754, -135.044, 64.077, 107.990]
    joint_pos1 = [60.164, -104.046, -20.299, -157.828, 53.871, 108.125]
    joint_pos2 = [58.054, -107.816, -15.798, -153.559, 49.501, 108.121]
    joint_pos3 = [55.266, -89.767, -46.349, -128.985, 45.001, 108.13]

    # 笛卡尔位置定义
    desc_safe = [423.659, -51.518, 366.413, -163.442, 32.248, -165.661]
    desc_pos1 = [409.950, 35.714, 272.466, -142.158, -1.209, -134.392]
    desc_pos2 = [456.062, 47.663, 291.916, -139.201, 4.688, -135.673]
    desc_pos3 = [485.838, 25.316, 313.259, -137.616, 17.480, -138.072]

    # 扩展轴位置定义
    eposSafe = [35.00, 25.00, 0.000, 0.000]
    epos1 = [35.00, 25.00, 0.000, 0.000]
    epos2 = [35.00, -25.000, 0.000, 0.000]
    epos3 = [35.00, -60.000, 0.000, 0.000]
    epos4 = [35.00, 0.000, 0.000, 0.000]
    epos5 = [35.00, 0.000, 0.000, 0.000]

    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    tool = 1
    user = 0
    vel = 100
    acc = 100
    ovl = 100
    blendT = -1
    blendR = -1
    rtn = 0

    # ========== MoveJ 测试 ==========
    print("--- MoveJ 测试 ---")
    robot.MoveJ(joint_pos=joint_safe, tool=tool, user=user, vel=vel, acc=acc, ovl=ovl,
                exaxis_pos=eposSafe, blendT=blendT, offset_flag=0, offset_pos=offset_pos)
    robot.ExtAxisMove(eposSafe, 100, -1)

    # ExtAxisSyncMoveJ: joint_pos, tool, user, exaxis_pos 为必填参数
    rtn = robot.ExtAxisSyncMoveJ(joint_pos=joint_pos1, tool=tool, user=user, exaxis_pos=epos1,
                                 vel=vel, acc=acc, ovl=ovl, blendT=blendT,
                                 offset_flag=0, offset_pos=offset_pos)
    rtn = robot.ExtAxisSyncMoveJ(joint_pos=joint_pos3, tool=tool, user=user, exaxis_pos=epos2,
                                 vel=vel, acc=acc, ovl=ovl, blendT=blendT,
                                 offset_flag=0, offset_pos=offset_pos)
    print(f"ExtAxisSyncMoveJ rtn is: {rtn}")

    # # ========== MoveL 测试 ==========
    print("--- MoveL 测试 ---")
    # robot.MoveJ(joint_pos=joint_safe, tool=tool, user=user, vel=vel, acc=acc, ovl=ovl,
    #             exaxis_pos=eposSafe, blendT=blendT, offset_flag=0, offset_pos=offset_pos)
    # robot.ExtAxisMove(eposSafe, 100, -1)
    #
    # # ExtAxisSyncMoveL: desc_pos, tool, user, exaxis_pos, joint_pos 为必填参数
    # # joint_pos 在源码中为必填参数，这里传入空列表或当前关节位置
    # rtn = robot.ExtAxisSyncMoveL(desc_pos=desc_pos3, tool=tool, user=user,
    #                              exaxis_pos=epos3, joint_pos=joint_pos3,
    #                              vel=vel, acc=acc, ovl=ovl, blendR=blendR,
    #                              offset_flag=0, offset_pos=offset_pos)
    # print(f"ExtAxisSyncMoveL rtn is: {rtn}")

    # ========== MoveC 测试 ==========
    # print("--- MoveC 测试 ---")
    # robot.MoveJ(joint_pos=joint_safe, tool=tool, user=user, vel=vel, acc=acc, ovl=ovl,
    #             exaxis_pos=eposSafe, blendT=blendT, offset_flag=0, offset_pos=offset_pos)
    # robot.ExtAxisMove(eposSafe, 100, -1)
    #
    # # ExtAxisSyncMoveC: desc_pos_p, tool_p, user_p, exaxis_pos_p, desc_pos_t, tool_t, user_t, exaxis_pos_t 为必填参数
    # rtn = robot.ExtAxisSyncMoveC(desc_pos_p=desc_pos2, tool_p=tool, user_p=user, vel_p=vel, acc_p=acc,
    #                              exaxis_pos_p=epos2, offset_flag_p=0, offset_pos_p=offset_pos,
    #                              desc_pos_t=desc_pos3, tool_t=tool, user_t=user, vel_t=vel, acc_t=acc,
    #                              exaxis_pos_t=epos3, offset_flag_t=0, offset_pos_t=offset_pos, ovl=ovl, blendR=-1,config=-1)
    # print(f"ExtAxisSyncMoveC rtn is: {rtn}")

# SetCoderCompenParams_test(robot)
# TestOriginPointWeave(robot)
#mode_test(robot)
# moveL_test(robot)
SetTrajectoryJSpeed_test(robot)
# ExtAxisSync_test(robot)