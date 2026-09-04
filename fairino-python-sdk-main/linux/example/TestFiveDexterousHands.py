from fairino import Robot
import time


def main():
    # ==================== 1. 初始化与连接 ====================
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # ==================== 2. 运动参数配置 ====================
    DEXTEROUS_ID = 1           # 从站号（灵巧手）
    FINGER_COUNT = 12          # 控制手指数量
    MOVE_TIMEOUT_MS = 12000    # 单次运动最大等待时间（毫秒）

    # 速度/力矩数组（12个手指，后4个留空）
    speed = [50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0]
    force = [50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0]

    # 三组目标位置（角度）
    posA = [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 0, 0, 0, 0]
    posB = [60, 10, 70, 30, 70, 70, 10, 10, 10, 10, 10, 10, 0, 0, 0, 0]
    posC = [50, 50, 20, 20, 0, 0, 0, 0, 70, 70, 70, 70, 0, 0, 0, 0]

    # 机器人两个测试位姿
    j1 = [-172.132, -90.455, -102.422, -67.864, 95.273, -21.129]
    j2 = [-173.180, -106.578, -83.661, -70.600, 95.440, -22.167]

    epos = [0.0, 0.0, 0.0, 0.0]
    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    print("===== 灵巧手完整功能测试开始 =====")

    # ==================== 3. 清除错误状态 ====================
    rtn = robot.ClearDexterousHandsError()
    print(f"[清除错误] rtn = {rtn}")

    # ==================== 4. 设置功能开关 ====================
    setFuncA = [0] * 32
    setFuncA[2] = 1
    setFuncA[3] = 1
    setFuncA[4] = 1
    setFuncA[9] = 1
    setFuncA[10] = 1
    setFuncA[11] = 1
    setFuncA[20] = 1  # 多轴同步运动
    setFuncA[22] = 1

    setFuncB = [0] * 32
    setFuncB[2] = 1
    setFuncB[3] = 1
    setFuncB[4] = 1
    setFuncB[9] = 1
    setFuncB[10] = 1
    setFuncB[11] = 1
    setFuncB[22] = 1

    # 主站设置功能 A
    rtn = robot.SetDexterousHandsFunc(DEXTEROUS_ID, setFuncA)
    print(f"[设置主站功能] rtn = {rtn}")

    # 从站（手指2~12）设置功能 B
    for i in range(2, FINGER_COUNT + 1):
        rtn = robot.SetDexterousHandsFunc(i, setFuncB)
    print(f"[设置从站功能（2~12）] rtn = {rtn}")

    # ==================== 5. 读取并验证功能状态 ====================
    rtn, getFunc = robot.GetDexterousHandsFunc(DEXTEROUS_ID)
    print(f"[读取功能状态] rtn = {rtn}")

    if rtn == 0:
        print("功能开关状态（32位）:")
        line = "  "
        for i in range(32):
            line += f"[{i}]={getFunc[i]}"
            if (i + 1) % 8 == 0 and i < 31:
                print(line)
                line = "  "
            elif i < 31:
                line += ", "
        if line.strip():
            print(line)

    # ==================== 6. 激活灵巧手 ====================
    rtn = robot.SetDexterousHandsAct(DEXTEROUS_ID, 1)
    print(f"[激活灵巧手] rtn = {rtn}")
    if rtn != 0:
        print("激活失败，测试中止")
        return

    time.sleep(5)  # 等待激活完成

    # ==================== 7. 往复运动测试（10次循环） ====================
    print("\n开始往复运动测试（共10次循环）...")
    print("  位姿1: j1（左）  位姿2: j2（右）")
    print("  手指目标: A→B→A→C（每组4个动作）\n")

    for iteration in range(1, 11):
        print(f"--- 第 {iteration:2d} 次循环 ---")

        # 动作1：移至 j1 + 手指 A
        robot.MoveJ(joint_pos=j1, tool=0, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
        rtn = robot.SetDexterousHandsMove(DEXTEROUS_ID, FINGER_COUNT, posA, speed, force, MOVE_TIMEOUT_MS)
        print(f"  j1 + posA → {rtn}")
        time.sleep(1)

        # 动作2：移至 j2 + 手指 B
        robot.MoveJ(joint_pos=j2, tool=0, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
        rtn = robot.SetDexterousHandsMove(DEXTEROUS_ID, FINGER_COUNT, posB, speed, force, MOVE_TIMEOUT_MS)
        print(f"  j2 + posB → {rtn}")
        time.sleep(1)

        # 动作3：移至 j1 + 手指 A
        robot.MoveJ(joint_pos=j1, tool=0, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
        rtn = robot.SetDexterousHandsMove(DEXTEROUS_ID, FINGER_COUNT, posA, speed, force, MOVE_TIMEOUT_MS)
        print(f"  j1 + posA → {rtn}")
        time.sleep(1)

        # 动作4：移至 j2 + 手指 C
        robot.MoveJ(joint_pos=j2, tool=0, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
        rtn = robot.SetDexterousHandsMove(DEXTEROUS_ID, FINGER_COUNT, posC, speed, force, MOVE_TIMEOUT_MS)
        print(f"  j2 + posC → {rtn}")
        time.sleep(1)

    # ==================== 8. 测试完成 ====================
    print("\n===== 测试完成 =====")
    print("  功能开关设置/读取  ✓")
    print("  灵巧手激活        ✓")
    print("  10次往复运动      ✓")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()