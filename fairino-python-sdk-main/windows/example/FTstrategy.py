from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    print("========== FT力控策略测试开始 ==========")

    # ========== FT_SpiralSearch: strategy 0 / 1 ==========
    print("=== FT_SpiralSearch strategy=0 ===")
    rtn = robot.FT_SpiralSearch(0, 5, 5.0, 3000.0, 3.0, 0)
    print(f"FT_SpiralSearch(0) rtn is {rtn}")
    time.sleep(5)
    robot.ResetAllError()
    time.sleep(2)

    print("=== FT_SpiralSearch strategy=1 ===")
    rtn = robot.FT_SpiralSearch(0, 5, 1.0, 3000.0, 3.0, 1)
    print(f"FT_SpiralSearch(1) rtn is {rtn}")
    time.sleep(5)
    robot.ResetAllError()
    time.sleep(2)

    # ========== FT_LinInsertion: strategy 0/1 ==========
    print("=== FT_LinInsertion strategy=0 ===")
    rtn = robot.FT_LinInsertion(0, 20.0, 15.0, 1.0, 10.0, 1, 0)
    print(f"FT_LinInsertion(0) rtn is {rtn}")
    time.sleep(5)
    robot.ResetAllError()
    time.sleep(2)

    print("=== FT_LinInsertion strategy=1 ===")
    rtn = robot.FT_LinInsertion(0, 20.0, 15.0, 1.0, 10.0, 1, 1)
    print(f"FT_LinInsertion(1) rtn is {rtn}")
    time.sleep(5)
    robot.ResetAllError()
    time.sleep(2)

    # ========== FT_FindSurface: strategy 0/1 ==========
    print("=== FT_FindSurface strategy=0 ===")
    rtn = robot.FT_FindSurface(0, 1, 1, 15.0, 5, 50.0, 20.0, 0)
    print(f"FT_FindSurface(0) rtn is {rtn}")
    time.sleep(10)
    robot.ResetAllError()
    time.sleep(5)

    print("=== FT_FindSurface strategy=1 ===")
    rtn = robot.FT_FindSurface(0, 1, 1, 15.0, 5, 50.0, 20.0, 1)
    print(f"FT_FindSurface(1) rtn is {rtn}")
    time.sleep(10)
    robot.ResetAllError()

    print("finish")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()