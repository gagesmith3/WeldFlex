from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # 关节位置定义
    j1 = [10.220, -11.121, -118.086, -46.739, 82.036, 131.503]
    j2 = [89.782, -11.122, -118.086, -46.740, 82.036, 131.504]

    epos = [0.0, 0.0, 0.0, 0.0]
    offset_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    # 设置基础速度
    robot.SetSpeed(20)

    # 初始最大关节速度数组
    maxJointVel = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]

    # 先关闭减速功能，移动到j2
    rtn = robot.SetVelReducePara(0, 200, 0, maxJointVel)
    robot.MoveJ(joint_pos=j2, tool=1, user=2, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

    # ========== 第一次：未启用减速（速度上限100°/s） ==========
    rtn = robot.SetVelReducePara(2, 200, 0, maxJointVel)
    print(f"SetVelReduceParaA param error rtn is {rtn}")

    robot.MoveJ(joint_pos=j1, tool=1, user=2, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
    robot.MoveJ(joint_pos=j2, tool=1, user=2, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

    # ========== 第二次：启用减速（速度上限20°/s） ==========
    maxJointVel = [20.0, 20.0, 20.0, 20.0, 20.0, 20.0]
    rtn = robot.SetVelReducePara(2, 200, 0, maxJointVel)
    print(f"SetVelReduceParaB reduce vel rtn is {rtn}")

    robot.MoveJ(joint_pos=j1, tool=1, user=2, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)
    robot.MoveJ(joint_pos=j2, tool=1, user=2, vel=100, acc=100, ovl=100,
                exaxis_pos=epos, blendT=-1, offset_flag=0, offset_pos=offset_pos)

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()