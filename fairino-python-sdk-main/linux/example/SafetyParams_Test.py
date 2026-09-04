from time import sleep
import time
from fairino import Robot

# 与机器人控制器建立连接
robot = Robot.RPC('192.168.58.2')


def test(self):
    error, status, hex_code = robot.GetSafetyParamsCheckSum()
    print(f"GetSafetyParamsChecksum: error={error}, status={status}, hex_code={hex_code}")
    time.sleep(3)
    error = robot.SafetyOPPasswordCheck(0, "12345678")
    print(f"SafetyOPPasswordCheck: error={error}")

    if error == 0:
        error = robot.SetAnticollision(mode=0, level=[2.0, 2.0, 2.0, 2.0, 2.0, 2.0], config=1)
        print(f"SetAnticollision: error={error}")

        error = robot.SetCollisionStrategy(strategy=0, safeTime=1000, safeDistance=150,
                                           safetyMargin=[10, 10, 10, 10, 10, 10])
        print(f"SetCollisionStrategy: error={error}")

    time.sleep(1)

    error, status, hex_code = robot.GetSafetyParamsCheckSum()
    print(f"GetSafetyParamsChecksum(again): error={error}, status={status}, hex_code={hex_code}")


test(robot)