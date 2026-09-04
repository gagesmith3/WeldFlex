from fairino import Robot
import time
# 与机器人控制器建立连接
robot = Robot.RPC('192.168.58.2')

def main(self):

    # 参数定义
    id = 1               # 从站号
    slaveNum = 4         # 控制4个手指
    max_time = 8000      # 最大等待时间 8秒
    speed = [0] * 16     # 速度数组，全0表示使用默认速度
    force = [0] * 16     # 力矩数组

    # 初始化力矩数组：前4个手指设为50%，其余为0
    for i in range(16):
        force[i] = 50 if i < 4 else 0

    # 定义辅助函数：设置位置数组（前4个手指有效）
    def set_positions(v1, v2, v3, v4):
        pos = [0.0] * 16
        pos[0] = v1
        pos[1] = v2
        pos[2] = v3
        pos[3] = v4
        return pos

    # 关节位置
    j1 = [-53.426,-85.916,109.280,-86.236,-96.663,-28.560]
    j2 = [-91.877,-85.917,109.281,-86.236,-96.663,-28.560]
    epos = [0, 0, 0, 0]
    offset_pos = [0, 0, 0, 0, 0, 0]

    print("===== 灵巧手完整功能测试开始 =====")

    # 1. 清除错误
    ret = robot.ClearDexterousHandsError()
    print(f"ClearDexterousHandsError -> {ret}")

    # ========== 2. 设置功能开关 ==========
    setFunc = [0] * 32
    setFunc[2] = 1   # 启用位置设置功能
    setFunc[4] = 1   # 启用力矩设置功能
    setFunc[9] = 1   # 读位置
    setFunc[10] = 1  # 读力矩
    setFunc[11] = 1  # 读状态
    setFunc[22] = 1  # 单轴运动状态

    ret = robot.SetDexterousHandsFunc(id, setFunc)
    print(f"SetDexterousHandsFunc(使能+初始化+位置/速度/力矩功能启用) -> {ret}")

    # ========== 3. 读取功能状态（验证设置是否生效） ==========
    ret, getFunc = robot.GetDexterousHandsFunc(id)
    print(f"GetDexterousHandsFunc -> {ret}")
    if ret == 0:
        # 打印全部32个数值
        print("GetDexterousHandsFunc 返回的全部32个数值:")
        for i in range(len(getFunc)):
            print(f"  [{i}]={getFunc[i]}", end="")
            if (i + 1) % 8 == 0:
                print()  # 每8个换行
            elif i < len(getFunc) - 1:
                print(", ", end="")
        if len(getFunc) % 8 != 0:
            print()  # 如果最后一行不足8个则补换行

    # ========== 4. 激活灵巧手 ==========
    ret = robot.SetDexterousHandsAct(id, 1)
    print(f"SetDexterousHandsAct(激活) -> {ret}")
    if ret != 0:
        print("激活失败，测试中止")
        return

    # ========== 5. 初始移动到 20°（通过Move指令下发位置和力矩数值） ==========
    pos = set_positions(20, 20, 20, 20)
    ret = robot.SetDexterousHandsMove(id, slaveNum, pos, speed, force, max_time)
    print(f"初始移动 20° -> {ret}")
    time.sleep(5)

    # ========== 6. 往复运动10次（10° ↔ 50°） ==========
    print("开始往复运动10次...")
    for iteration in range(1, 11):
        robot.MoveJ(joint_pos=j1, tool=1, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

        pos = set_positions(10, 10, 10, 10)
        ret = robot.SetDexterousHandsMove(id, slaveNum, pos, speed, force, max_time)
        print(f"[{iteration}] 移动到 10° -> {ret}")
        time.sleep(1)

        robot.MoveJ(joint_pos=j2, tool=1, user=0, vel=100, acc=100, ovl=100,
                    exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

        pos = set_positions(50, 50, 50, 50)
        ret = robot.SetDexterousHandsMove(id, slaveNum, pos, speed, force, max_time)
        print(f"[{iteration}] 移动到 50° -> {ret}")
        time.sleep(1)

    print("测试完成（功能开关设置/读取 + 激活 + 10次往复运动）。")


# 调用测试函数
main(robot)