from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # 关节位置定义
    j1 = [-11.188, -64.165, -107.299, -76.706, 89.590, 92.983]
    j2 = [-38.148, -97.408, -133.704, -30.999, 89.584, 92.986]
    j3 = [-25.561, -123.131, -85.736, -94.911, 89.582, 93.006]
    j4 = [-8.013, -125.881, -79.196, -84.440, 89.564, 93.005]
    j5 = [-2.722, -94.518, -119.965, -54.518, 89.563, 93.005]
    j6 = [-2.671, -56.234, -138.914, -25.099, 95.355, 92.967]
    j7 = [-1.229, -121.184, -63.201, -122.331, 93.045, 93.019]

    # 笛卡尔位置定义
    desc1 = [225.986, 190.694, 394.238, -6.230, -23.797, -98.972]
    desc2 = [52.741, 262.917, 30.824, -5.696, -9.864, -126.092]
    desc3 = [70.455, 88.410, 45.299, -4.101, 31.775, -113.199]
    desc4 = [209.453, -73.895, 56.416, -4.727, 17.523, -95.906]
    desc5 = [274.800, 81.106, 102.977, -5.467, -2.980, -90.711]
    desc6 = [300.392, 177.281, 300.926, -1.909, -51.894, -89.703]
    desc7 = [296.856, -31.294, 215.698, -0.589, 34.594, -88.954]

    exaxis = [0.0, 0.0, 0.0, 0.0]
    offset = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    tool = 1
    workpiece = 1
    blend = 5.0

    # ========== 第一次运动：未启用工件变换 ==========
    print("--- 第一次运动（未启用工件变换） ---")
    robot.MoveJ(joint_pos=j1, desc_pos=desc1, tool=tool, user=workpiece,
                vel=100, acc=100, ovl=100, exaxis_pos=exaxis,
                blendT=-1, offset_flag=0, offset_pos=offset)

    robot.MoveJ(joint_pos=j2, desc_pos=desc2, tool=tool, user=workpiece,
                vel=100, acc=100, ovl=100, exaxis_pos=exaxis,
                blendT=blend, offset_flag=0, offset_pos=offset)

    robot.MoveL(joint_pos=j3, desc_pos=desc3, tool=tool, user=workpiece,
                vel=10, acc=100, ovl=100, blendR=blend, blendMode=0,
                exaxis_pos=exaxis, search=0, offset_flag=1, offset_pos=offset)

    # MoveC: 中间点使用 desc_pos_p, tool_p, user_p, exaxis_pos_p
    #         终点使用 desc_pos_t, tool_t, user_t, exaxis_pos_t
    robot.MoveC(desc_pos_p=desc4, tool_p=tool, user_p=workpiece,
                desc_pos_t=desc5, tool_t=tool, user_t=workpiece,
                joint_pos_p=j4, joint_pos_t=j5,
                vel_p=100, acc_p=100, exaxis_pos_p=exaxis, offset_flag_p=0, offset_pos_p=offset,
                vel_t=100, acc_t=100, exaxis_pos_t=exaxis, offset_flag_t=0, offset_pos_t=offset,
                ovl=10, blendR=blend)

    # Circle: 中间点使用 desc_pos_p, tool_p, user_p, exaxis_pos_p
    #         终点使用 desc_pos_t, tool_t, user_t, exaxis_pos_t
    #         与MoveC参数结构一致
    robot.Circle(desc_pos_p=desc6, tool_p=tool, user_p=workpiece,
                 desc_pos_t=desc7, tool_t=tool, user_t=workpiece,
                 joint_pos_p=j6, joint_pos_t=j7,
                 vel_p=100, acc_p=100, exaxis_pos_p=exaxis,
                 vel_t=100, acc_t=100, exaxis_pos_t=exaxis,
                 ovl=10, offset_flag=0, offset_pos=offset, blendR=blend)

    # ========== 启用工件变换 ==========
    print("\n--- 启用工件变换 ---")
    rtn = robot.WorkPieceTrsfStart(2)
    print(f"WorkPieceTrsfStart rtn is {rtn}")

    # ========== 第二次运动：启用工件变换 ==========
    print("--- 第二次运动（已启用工件变换） ---")
    robot.MoveJ(joint_pos=j1, desc_pos=desc1, tool=tool, user=workpiece,
                vel=100, acc=100, ovl=100, exaxis_pos=exaxis,
                blendT=-1, offset_flag=0, offset_pos=offset)

    robot.MoveJ(joint_pos=j2, desc_pos=desc2, tool=tool, user=workpiece,
                vel=100, acc=100, ovl=100, exaxis_pos=exaxis,
                blendT=blend, offset_flag=0, offset_pos=offset)

    robot.MoveL(joint_pos=j3, desc_pos=desc3, tool=tool, user=workpiece,
                vel=10, acc=100, ovl=100, blendR=blend, blendMode=0,
                exaxis_pos=exaxis, search=0, offset_flag=1, offset_pos=offset)

    # MoveC: 中间点使用 desc_pos_p, tool_p, user_p, exaxis_pos_p
    #         终点使用 desc_pos_t, tool_t, user_t, exaxis_pos_t
    robot.MoveC(desc_pos_p=desc4, tool_p=tool, user_p=workpiece,
                desc_pos_t=desc5, tool_t=tool, user_t=workpiece,
                joint_pos_p=j4, joint_pos_t=j5,
                vel_p=100, acc_p=100, exaxis_pos_p=exaxis, offset_flag_p=0, offset_pos_p=offset,
                vel_t=100, acc_t=100, exaxis_pos_t=exaxis, offset_flag_t=0, offset_pos_t=offset,
                ovl=10, blendR=blend)

    # Circle: 中间点使用 desc_pos_p, tool_p, user_p, exaxis_pos_p
    #         终点使用 desc_pos_t, tool_t, user_t, exaxis_pos_t
    #         与MoveC参数结构一致
    robot.Circle(desc_pos_p=desc6, tool_p=tool, user_p=workpiece,
                 desc_pos_t=desc7, tool_t=tool, user_t=workpiece,
                 joint_pos_p=j6, joint_pos_t=j7,
                 vel_p=100, acc_p=100, exaxis_pos_p=exaxis,
                 vel_t=100, acc_t=100, exaxis_pos_t=exaxis,
                 ovl=10, offset_flag=0, offset_pos=offset, blendR=blend)

    # ========== 结束工件变换 ==========
    rtn = robot.WorkPieceTrsfEnd()
    print(f"WorkPieceTrsfEnd rtn is {rtn}")

    # 关闭连接
    robot.CloseRPC()
    time.sleep(2)


if __name__ == "__main__":
    main()