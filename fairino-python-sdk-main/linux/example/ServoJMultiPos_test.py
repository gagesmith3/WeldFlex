from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # ========== 读取关节轨迹文件 ==========
    file_path = "C://Users/lenovo/Desktop/ServoJPath.txt"
    all_joint_data = []

    try:
        with open(file_path, 'r') as fin:
            for line in fin:
                parts = line.strip().split()
                if len(parts) >= 6:
                    # 跳过第一列（索引列），取第2~7列（索引1~6）
                    one_pose = [float(parts[i]) for i in range(1, 7)]
                    all_joint_data.append(one_pose)
    except FileNotFoundError:
        print("无法打开文件 ServoJPath.txt")
        return

    print(f"一共读取到 {len(all_joint_data)} 组关节位置")

    # ========== 构建往返路径（正向 + 反向，去掉重复端点） ==========
    back_forth_path = all_joint_data.copy()
    # 从倒数第二个开始反向追加（去掉重复的终点）
    for i in range(len(all_joint_data) - 2, -1, -1):
        back_forth_path.append(all_joint_data[i])

    print(f"往返路径总点数: {len(back_forth_path)}")

    epos = [0.0, 0.0, 0.0, 0.0]
    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    # 移动到起始位置
    robot.MoveJ(joint_pos=all_joint_data[0], tool=0, user=0,
                vel=100, acc=100, ovl=100, exaxis_pos=epos,
                blendT=-1, offset_flag=0, offset_pos=offset_pos)

    time.sleep(1)

    # ========== 主循环 ==========
    while True:
        # 复位所有错误
        robot.ResetAllError()

        # 移动到起始位置
        robot.MoveJ(joint_pos=all_joint_data[0], tool=0, user=0,
                    vel=100, acc=100, ovl=100, exaxis_pos=epos,
                    blendT=-1, offset_flag=0, offset_pos=offset_pos)

        move_count = 0
        total_points = len(back_forth_path)

        while move_count < total_points - 10:
            # 获取机器人实时状态
            rtn, pkg = robot.GetRobotRealTimeState()

            # 计算单次可发送的ServoJ命令数量
            single_servo_j_count = 50 - pkg.mc_queue_len
            if single_servo_j_count <= 0:
                time.sleep(0.1)
                continue

            if single_servo_j_count > 10:
                single_servo_j_count = 10

            # 构建本次要发送的关节位置列表（二维列表，每个元素包含6个关节角度）
            joint_pos_list = []
            for j in range(single_servo_j_count):
                if move_count < total_points:
                    joint_pos_list.append(back_forth_path[move_count])
                    move_count += 1
                else:
                    break

            # 发送多点ServoJ命令
            axis_pos = [0.0, 0.0, 0.0, 0.0]
            # 注意参数顺序: joint_pos, axisPos, acc, vel, cmdT, filterT, gain, id, cmdType
            # acc=100.0 对应C++的100.0, vel=100.0 对应C++的100.0
            rtn, servo_j_cmd_count = robot.ServoJMultiPos(joint_pos_list, axis_pos,
                                                          acc=100.0, vel=100.0,
                                                          cmdT=0.008, filterT=0.008,
                                                          gain=1.0, id=0, cmdType=0)
            if rtn != 0:
                print(f"ServoJMultiPos 执行失败，错误码: {rtn}")
                break

            # 打印调试信息
            print(f"发送 {len(joint_pos_list)} 个点位，ServoJ命令计数: {servo_j_cmd_count}")

        time.sleep(4)

    # 关闭连接（正常情况下不会执行到这里）
    robot.CloseRPC()


if __name__ == "__main__":
    main()