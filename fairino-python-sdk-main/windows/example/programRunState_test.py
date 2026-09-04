from fairino import Robot
from fairino.Robot import RobotState, AddRobotRealtimeState
import time


def main():
    # 添加需要获取的实时状态数据：程序运行状态
    rtn = AddRobotRealtimeState([RobotState.ProgramRunState])
    if rtn != 0:
        print(f"✗ 添加字段失败，错误码: {rtn}")
        return None
    print("✓ 字段添加成功")

    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    time.sleep(0.1)  # 100ms

    while True:
        rtn, pkg = robot.GetRobotRealTimeState()
        print(f"runstate is {pkg.programRunState}")
        time.sleep(0.1)  # 100ms

    # 关闭连接（正常情况下不会执行到这里，因为while循环是无限的）
    robot.CloseRPC()


if __name__ == "__main__":
    main()