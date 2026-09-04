from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    def callback(src_type, count, cmd_id, data_len, content):
        print("收到回复: cmd_id={} count={} data_len={} content={}".format(cmd_id, count, data_len, content))
        return 0

    robot.SetUDPCmdRpyCallback(callback)

    # 关节位置定义
    j1 = [9.000, -66.067, 67.706, -103.217, -90.151, 100.669]
    j2 = [-4.660, -107.973, 103.734, -76.214, -89.999, 90.886]
    j3 = [-36.762, -77.380, 91.364, -127.159, -90.024, 54.833]
    j4 = [-62.875, -89.460, 86.437, -77.030, -90.012, 31.539]

    # 笛卡尔位置定义
    desc_pos1 = [-654.129, -235.344, 246.543, 6.010, -11.535, -176.787]
    desc_pos2 = [-273.710, -100.871, 280.935, 5.692, 9.522, 179.512]
    desc_pos3 = [-566.093, 311.278, 215.008, -10.453, -17.486, -174.209]
    desc_pos4 = [-246.558, 328.240, 292.173, 13.912, 4.437, -179.067]

    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    epos = [0.0, 0.0, 0.0, 0.0]

    tool = 2
    user = 0
    vel = 100.0
    acc = 100.0
    ovl = 20.0
    oacc = 100.0
    blendT = 0.0
    blendR = 0.0
    flag = 0
    search = 0
    blendMode = 0
    velAccMode = 0

    # 设置速度
    robot.SetSpeed(1)

    robot.WeaveEnd(0)

    # 设置摆动回中心配置
    rtn = robot.SetWeaveBackCenterConfig(1)
    print(f"SetWeaveBackCenterConfig rtn is {rtn}")

    # 获取摆动回中心配置
    rtn, weaveBackConfig = robot.GetWeaveBackCenterConfig()
    print(f"GetWeavebackCenterConfig {weaveBackConfig}")

    # 移动到起始位置
    rtn = robot.MoveJ(joint_pos=j1, desc_pos=desc_pos1, tool=tool, user=user,
                      vel=vel, acc=acc, ovl=100.0, exaxis_pos=epos,
                      blendT=blendT, offset_flag=flag, offset_pos=offset_pos)
    print(f"MoveJ rtn is {rtn}")

    # 开始摆动
    robot.WeaveStart(0)

    # 开始样条曲线
    robot.NewSplineStart(0, 6000)

    # 添加样条曲线点
    rtn = robot.NewSplinePoint(joint_pos=j1, desc_pos=desc_pos1, tool=tool, user=user,lastFlag=0,
                               vel=vel, acc=acc, ovl=ovl, blendR=-1, config=0)
    print(f"NewSplinePoint 1 rtn is {rtn}")

    rtn = robot.NewSplinePoint(joint_pos=j2, desc_pos=desc_pos2, tool=tool, user=user,lastFlag=0,
                               vel=vel, acc=acc, ovl=ovl, blendR=-1, config=0)
    print(f"NewSplinePoint 2 rtn is {rtn}")

    rtn = robot.NewSplinePoint(joint_pos=j3, desc_pos=desc_pos3, tool=tool, user=user,lastFlag=0,
                               vel=vel, acc=acc, ovl=ovl, blendR=-1, config=0)
    print(f"NewSplinePoint 3 rtn is {rtn}")

    rtn = robot.NewSplinePoint(joint_pos=j4, desc_pos=desc_pos4, tool=tool, user=user,lastFlag=1,
                               vel=vel, acc=acc, ovl=ovl, blendR=-1, config=1)
    print(f"NewSplinePoint 4 rtn is {rtn}")

    # 结束样条曲线
    robot.NewSplineEnd()

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()