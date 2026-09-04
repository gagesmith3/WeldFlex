from fairino import Robot
import time
import threading


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    print("============================================================")
    print("  摆动调速与实时偏移测试")
    print("============================================================")

    # 点位定义
    epos = [0.0, 0.0, 0.0, 0.0]
    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    j1 = [5.027, -84.331, -75.139, -103.690, 86.379, 20.794]
    d1 = [324.752, -83.339, 366.314, -172.321, -0.936, -106.047]

    j2 = [-35.335, -117.598, -57.174, -95.234, 90.001, -19.560]
    d2 = [324.999, -355.439, 260.000, 179.995, 0.003, -105.775]

    j3 = [59.787, -117.594, -57.183, -95.222, 90.006, 75.562]
    d3 = [324.998, 355.441, 260.002, 179.995, 0.003, -105.775]

    rtn = 0

    # ---- Step 1: MoveJ到起始点 ----
    print("\nStep 1: MoveJ to start point")
    rtn = robot.MoveJ(joint_pos=j1, desc_pos=d1, tool=1, user=0, vel=100, acc=100, ovl=50,
                      exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
    print(f"  MoveJ(j1) rtn={rtn}")
    time.sleep(0.5)

    # ---- Step 2: MoveJ到摆动入口 ----
    print("\nStep 2: MoveJ to weave entry point")
    rtn = robot.MoveJ(joint_pos=j2, desc_pos=d2, tool=1, user=0, vel=100, acc=100, ovl=50,
                      exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
    print(f"  MoveJ(j2) rtn={rtn}")
    time.sleep(0.5)

    # ---- Step 3: WeaveStart, 启动摆动MoveL线程 ----
    print("\nStep 3: WeaveStart + MoveL in background thread")
    robot.WeaveStart(0)

    weave_running = True
    move_rtn = [0]  # 使用列表以便在线程中修改

    def weave_move_thread():
        rtn_val = robot.MoveL(joint_pos=j3, desc_pos=d3, tool=1, user=0, vel=100, acc=100, ovl=5,
                              blendR=-1, offset_flag=0, exaxis_pos=epos, search=0,
                              offset_pos=offset_pos, config=5, velAccParamMode=0, overSpeedStrategy=0, speedPercent=10)
        print(f"  MoveL(weave) thread finished, rtn={rtn_val}")
        move_rtn[0] = rtn_val
        nonlocal weave_running
        weave_running = False

    weave_thread = threading.Thread(target=weave_move_thread)
    weave_thread.daemon = True
    weave_thread.start()
    time.sleep(0.5)  # 等待运动开始

    # ---- Step 4: 调速测试(主线程，摆动MoveL后台运行) ----
    print("\nStep 4: SetSpeed test during weaving")
    speed_values = [20, 50, 80, 30, 60, 10]
    for speed in speed_values:
        if not weave_running:
            break
        rtn = robot.SetSpeedInstant(speed)
        print(f"  SetSpeed({speed}) -> rtn={rtn}")
        # rtn, pkg = robot.GetRobotRealTimeState()
        # print(f"target_TCP_CmpSpeed: [{', '.join([f'{x:.3f}' for x in pkg.target_TCP_CmpSpeed])}]")
        time.sleep(5)

    time.sleep(5)

    # ---- Step 5: SetWeaveOffsetRT偏移测试(主线程，摆动MoveL后台运行) ----
    print("\nStep 5: SetWeaveOffsetRT test (50 iterations, delta=0.1)")
    accum_offset = 0.0
    for i in range(50):
        if not weave_running:
            break
        accum_offset += 1
        weave_offset = [0.0, 0.0, accum_offset, 0.0, 0.0, 0.0]
        rtn = robot.SetWeaveOffsetRT(weave_offset)
        rtn, pkg = robot.GetRobotRealTimeState()
        print(f"  [{i+1}/50] SetWeaveOffsetRT(x={accum_offset:.1f}) -> rtn={rtn}, "
              f"TCP_pos=({pkg.tl_cur_pos[0]:.2f},{pkg.tl_cur_pos[1]:.2f},{pkg.tl_cur_pos[2]:.2f})")
        time.sleep(0.1)

    # ---- Step 6: 等待摆动MoveL完成, WeaveEnd ----
    print("\nStep 6: Wait for weave MoveL, then WeaveEnd")
    weave_thread.join()
    robot.WeaveEnd(0)
    time.sleep(0.5)

    # ---- Step 7: MoveL返回起始点 ----
    print("\nStep 7: MoveL back to start")
    rtn = robot.MoveL(joint_pos=j1, desc_pos=d1, tool=1, user=0, vel=100, acc=100, ovl=50,
                      blendR=-1, offset_flag=0, exaxis_pos=epos, search=0,
                      offset_pos=offset_pos, config=50, velAccParamMode=0, overSpeedStrategy=0, speedPercent=10)
    print(f"  MoveL(back) rtn={rtn}")

    rtn, pkg = robot.GetRobotRealTimeState()
    print(f"\n  Final robot state: main_code={pkg.main_code}, sub_code={pkg.sub_code}")
    print("============================================================")
    print("  摆动调速与实时偏移测试 完成")
    print("============================================================")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()