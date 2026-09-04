from fairino import Robot
import time
from datetime import datetime


def GripperDropAlarmTest(robot, gripperIndex=1, vel=50, force=50, testCycles=10):
    """
    夹爪工件掉落报警测试
    测试要求:
    1. 已通过web配置完成外设协议/使能/夹爪/CO2=53
    2. 夹爪安装在机器人末端
    3. 本函数通过SDK控制夹爪运动并检测工件掉落报警

    参数:
        robot: 已RPC连接的Robot实例
        gripperIndex: 夹爪索引(默认1)
        vel: 夹爪速度百分比
        force: 夹爪力矩百分比
        testCycles: 测试循环次数(默认10)

    返回:
        True: 测试通过; False: 测试失败
    """
    passCount = 0
    failCount = 0

    print("=== 夹爪工件掉落报警测试 开始 ===")
    print(f"测试次数: {testCycles}, 夹爪索引: {gripperIndex}")

    # 切换到手动模式
    rtn = robot.Mode(1)
    print(f"Mode(1) rtn={rtn}")

    for cycle in range(1, testCycles + 1):
        print(f"\n--- 第 {cycle}/{testCycles} 次测试 ---")
        cyclePass = True

        try:
            # 获取机器人实时状态
            rtn, pkg = robot.GetRobotRealTimeState()

            # 清除残留故障
            if pkg.main_code != 0 or pkg.sub_code != 0:
                print(f"  残留故障码[{pkg.main_code},{pkg.sub_code}]，先清除")
                robot.ResetAllError()
                time.sleep(0.5)

            # 步骤1: 夹爪张开到0位置
            print("  步骤1: 夹爪张开到0位置...")
            rtn = robot.MoveGripper(gripperIndex, 0, vel, force, 10000, 1, 0, 0, 0, 0)
            if rtn != 0:
                print(f"  夹爪张开失败, rtn={rtn}")
                failCount += 1
                continue

            # 步骤2: 放入工件
            print("  步骤2: 请放入工件... (等待3秒)")
            time.sleep(3)

            # 步骤3: 夹爪闭合到100位置
            print("  步骤3: 夹爪闭合到100位置...")
            rtn = robot.MoveGripper(gripperIndex, 100, vel, force, 10000, 1, 0, 0, 0, 0)
            if rtn != 0:
                print(f"  夹爪闭合失败, rtn={rtn}")
                failCount += 1
                continue
            time.sleep(4)

            # 步骤4: 拿掉工件
            print("  步骤4: 请拿掉工件... (等待2秒)")
            time.sleep(1)

            # 步骤5: 检测3000ms内报警 (main_code=8, sub_code=3 表示工件掉落)
            print("  步骤5: 检测报警...")
            alarmDetected = False
            startTime = datetime.now()
            alarmTimeout = 3000  # 毫秒

            while (datetime.now() - startTime).total_seconds() * 1000 < alarmTimeout:
                rtn, pkg = robot.GetRobotRealTimeState()

                if pkg.main_code == 8 and pkg.sub_code == 3:
                    elapsed = (datetime.now() - startTime).total_seconds() * 1000
                    print("  PASS: 检测到工件掉落报警")
                    print(f"    main_code={pkg.main_code}, sub_code={pkg.sub_code}")
                    print(f"    响应时间: {elapsed:.0f}ms")

                    # CO2状态: cl_dgt_output_h的第3位 (0x04)
                    co2State = 1 if (pkg.cl_dgt_output_h & 0x04) > 0 else 0
                    print(f"    CO2={co2State}")
                    print(f"    program_state={pkg.program_state} (1=停止)")

                    alarmDetected = True
                    break
                time.sleep(0.1)  # 100ms

            if not alarmDetected:
                print(f"  FAIL: 未在{alarmTimeout}ms内检测到报警")
                rtn, pkg = robot.GetRobotRealTimeState()
                print(f"    当前 main_code={pkg.main_code}, sub_code={pkg.sub_code}")
                cyclePass = False

            # 步骤6: 清除报警，复位+激活夹爪
            print("  步骤6: 清除报警...")
            rtn = robot.ResetAllError()
            print(f"  ResetAllError rtn={rtn}")
            time.sleep(1.5)

            # 复位夹爪
            print("  步骤7: 复位夹爪...")
            rtn = robot.ActGripper(gripperIndex, 0)
            print(f"  ActGripper(reset) rtn={rtn}")
            time.sleep(3)

            # 激活夹爪
            print("  步骤8: 激活夹爪...")
            rtn = robot.ActGripper(gripperIndex, 1)
            print(f"  ActGripper(activate) rtn={rtn}")
            time.sleep(5)

            rtn, pkg = robot.GetRobotRealTimeState()
            print(f"  最终状态: main_code={pkg.main_code}, sub_code={pkg.sub_code}")

            if pkg.main_code == 0 and pkg.sub_code == 0:
                print("  报警已清除, 夹爪已复位激活")
            else:
                print("  WARN: 状态未完全恢复")
                cyclePass = False

            if cyclePass:
                passCount += 1
            else:
                failCount += 1

        except Exception as e:
            print(f"  异常: {e}")
            failCount += 1

    print("\n=== 测试结果 ===")
    print(f"通过: {passCount}, 失败: {failCount}, 总计: {testCycles}")
    print(f"通过率: {passCount / testCycles * 100}%")

    return failCount == 0


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # 执行夹爪工件掉落报警测试
    result = GripperDropAlarmTest(robot, gripperIndex=1, vel=50, force=50, testCycles=10)

    if result:
        print("\n✓ 测试通过")
    else:
        print("\n✗ 测试失败")

    # 关闭连接
    robot.CloseRPC()


# 调用测试函数
if __name__ == "__main__":
    main()