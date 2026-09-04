from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    print("========== 夹爪等待运动完成测试 ==========")

    # ========== 夹爪张开 ==========
    rtn = robot.MoveGripper(1, 0, 100, 100, 30000, 0, 0, 0, 0, 0)
    print(f"MoveGripper(张开) ret={rtn}")
    time.sleep(4)

    # ========== 夹爪闭合 ==========
    rtn = robot.MoveGripper(1, 90, 100, 100, 30000, 0, 0, 0, 0, 0)
    print(f"MoveGripper(闭合) ret={rtn}")

    # ========== 等待运动完成（未检测到物体，超时30s，停止报错） ==========
    rtn = robot.GripperWaitMotionDone(1, 30000, 0, 0)
    print(f"GripperWaitMotionDone(等待完成未检测到物体) ret={rtn}")

    # ========== 夹爪张开 ==========
    rtn = robot.MoveGripper(1, 0, 100, 100, 30000, 0, 0, 0, 0, 0)
    print(f"MoveGripper(张开) ret={rtn}")

    print("========== 测试完成 ==========")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()