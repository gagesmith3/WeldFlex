from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    print("\n========== 传送带静止跟踪测试 ==========")

    # 点位定义
    j1 = [-35.146, -102.684, 120.805, -100.401, -90.295, 150.105]
    d1 = [-121.814, -348.341, 209.978, -173.152, -3.585, -5.446]

    ex = [0.0, 0.0, 0.0, 0.0]
    zeroOff = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    tool = 1
    workpiece = 1

    para = [0, 10000, 200, 0, 0, 10]
    # 设置传送带参数
    rtn = robot.ConveyorSetParam(para= para)
    print(f"ConveyorSetParam rtn is {rtn}")

    # 移动到起始位置
    robot.MoveJ(joint_pos=j1, desc_pos=d1, tool=tool, user=workpiece,
                vel=100, acc=100, ovl=100, exaxis_pos=ex,
                blendT=-1, offset_flag=0, offset_pos=zeroOff)

    # ========== Step 1: SetDO 控制信号 ==========
    print("--- Step 1: SetDO(6,1) ---")
    rtn = robot.SetDO(6, 1, 0, 0)
    print(f"  SetDO(6,1) rtn={rtn}")

    # ========== Step 2: 传送带跟踪开始 ==========
    print("--- Step 2: ConveyorTrackStart(2) ---")
    rtn = robot.ConveyorTrackStart(2)
    print(f"  ConveyorTrackStart(2) rtn={rtn}")

    # ========== Step 3: 工件IO检测 ==========
    print("--- Step 3: ConveyorIODetect(10000) ---")
    rtn = robot.ConveyorIODetect(10000)
    print(f"  ConveyorIODetect(10000) rtn={rtn}")

    # ========== Step 4: 获取跟踪数据 ==========
    print("--- Step 4: ConveyorGetTrackData(2) ---")
    rtn = robot.ConveyorGetTrackData(2)
    print(f"  ConveyorGetTrackData(2) rtn={rtn}")

    # ========== Step 5: 静止跟踪参数配置 ==========
    print("--- Step 5: SetStationaryTrackPara(0,5,5) ---")
    rtn = robot.SetStationaryTrackPara(0, 5, 5)
    print(f"  SetStationaryTrackPara(0,5,5) rtn={rtn}")

    # ========== Step 6: 执行静止跟踪运动 ==========
    print("--- Step 6: MoveStationary() ---")
    rtn = robot.MoveStationary()
    print(f"  MoveStationary() rtn={rtn}")

    rtn = robot.WaitStationaryMotionDone()
    print(f"  WaitStationaryMotionDone() rtn={rtn}")

    # ========== Step 7: 传送带跟踪结束 ==========
    print("--- Step 7: ConveyorTrackEnd() ---")
    rtn = robot.ConveyorTrackEnd()
    print(f"  ConveyorTrackEnd() rtn={rtn}")

    # ========== Step 8: SetDO 关闭信号 ==========
    print("--- Step 8: SetDO(6,0) ---")
    rtn = robot.SetDO(6, 0, 0, 0)
    print(f"  SetDO(6,0) rtn={rtn}")

    print("\n========== 静止跟踪测试完成 ==========")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()