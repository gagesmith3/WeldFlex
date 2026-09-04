from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    print("========== 安全IO配置测试 ==========")

    # ========== 设置安全DI配置 ==========
    print("\n--- 设置安全DI配置 ---")
    rtn = robot.SetSafetyDIConfig(0, 201)
    print(f"SetSafetyDIConfig(0, 201) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(1, 202)
    print(f"SetSafetyDIConfig(1, 202) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(2, 203)
    print(f"SetSafetyDIConfig(2, 203) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(3, 204)
    print(f"SetSafetyDIConfig(3, 204) rtn is {rtn}")

    # 获取DI配置
    rtn, getDIConfig = robot.GetDIConfig()
    print(f"GetDIConfig rtn is {rtn}, value is {getDIConfig[0]} {getDIConfig[1]} {getDIConfig[2]} {getDIConfig[3]} {getDIConfig[4]} {getDIConfig[5]} {getDIConfig[6]} {getDIConfig[7]}")

    # ========== 清除安全DI配置 ==========
    print("\n--- 清除安全DI配置 ---")
    rtn = robot.SetSafetyDIConfig(0, 0)
    print(f"SetSafetyDIConfig(0, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(1, 0)
    print(f"SetSafetyDIConfig(1, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(2, 0)
    print(f"SetSafetyDIConfig(2, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDIConfig(3, 0)
    print(f"SetSafetyDIConfig(3, 0) rtn is {rtn}")

    # 获取DI配置验证清除
    rtn, getDIConfig = robot.GetDIConfig()
    print(f"GetDIConfig rtn is {rtn}, value is {getDIConfig[0]} {getDIConfig[1]} {getDIConfig[2]} {getDIConfig[3]} {getDIConfig[4]} {getDIConfig[5]} {getDIConfig[6]} {getDIConfig[7]}")

    # ========== 设置安全DO配置 ==========
    print("\n--- 设置安全DO配置 ---")
    rtn = robot.SetSafetyDOConfig(0, 204)
    print(f"SetSafetyDOConfig(0, 204) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(1, 205)
    print(f"SetSafetyDOConfig(1, 205) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(2, 206)
    print(f"SetSafetyDOConfig(2, 206) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(3, 207)
    print(f"SetSafetyDOConfig(3, 207) rtn is {rtn}")

    # 获取DO配置
    rtn, getDOConfig = robot.GetDOConfig()
    print(f"GetDOConfig rtn is {rtn}, value is {getDOConfig[0]} {getDOConfig[1]} {getDOConfig[2]} {getDOConfig[3]} {getDOConfig[4]} {getDOConfig[5]} {getDOConfig[6]} {getDOConfig[7]}")

    # ========== 清除安全DO配置 ==========
    print("\n--- 清除安全DO配置 ---")
    rtn = robot.SetSafetyDOConfig(0, 0)
    print(f"SetSafetyDOConfig(0, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(1, 0)
    print(f"SetSafetyDOConfig(1, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(2, 0)
    print(f"SetSafetyDOConfig(2, 0) rtn is {rtn}")

    rtn = robot.SetSafetyDOConfig(3, 0)
    print(f"SetSafetyDOConfig(3, 0) rtn is {rtn}")

    # 获取DO配置验证清除
    rtn, getDOConfig = robot.GetDOConfig()
    print(f"GetDOConfig rtn is {rtn}, value is {getDOConfig[0]} {getDOConfig[1]} {getDOConfig[2]} {getDOConfig[3]} {getDOConfig[4]} {getDOConfig[5]} {getDOConfig[6]} {getDOConfig[7]}")

    time.sleep(2)

    # 关闭连接
    robot.CloseRPC()
    time.sleep(1)

    print("\n========== 测试完成 ==========")


if __name__ == "__main__":
    main()