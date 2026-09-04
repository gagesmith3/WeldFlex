from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    # ========== 激光记录复现 + 扩展轴异步运动 + 定点摆动 ==========
    def test_laser_record_replay_exaxis_with_wave():
        print("\n========== 激光记录复现 + 扩展轴异步运动 + 定点摆动 ==========")

        startjointPos = [105.600,-65.393,-93.638,-79.687,79.175,134.046]
        startdescPose = [42.376,533.597,362.564,-34.957,-0.564,169.063]
        endjointPos = [105.600,-65.393,-93.638,-79.687,79.175,134.046]
        enddescPose = [42.376,533.597,362.564,-34.957,-0.564,169.063]
        offdese = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        # MoveJ 到安全点(扩展轴 0,134.296,0,0)
        rtn = robot.MoveJ(joint_pos=startjointPos, desc_pos=startdescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=50, exaxis_pos=[0, 134.296, 0, 0],
                          blendT=-1, offset_flag=0, offset_pos=offdese)
        print(f"MoveJ start: {rtn}")

        # 扩展轴异步运动到 74.335 起始点
        rtn = robot.ExtAxisMove([0, 74.335, 0, 0], 50, -1)
        print(f"ExtAxisMove 74.335: {rtn}")
        time.sleep(3)

        # MoveL 到起始点
        rtn = robot.MoveL(joint_pos=endjointPos, desc_pos=enddescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=50, blendR=-1, offset_flag=0,
                          exaxis_pos=[0, 74.335, 0, 0], search=0,
                          offset_pos=offdese)
        print(f"MoveL end: {rtn}")

        # 开始激光记录
        rtn = robot.LaserSensorRecord1(2, 10)
        print(f"LaserSensorRecord start: {rtn}")

        # 记录过程中扩展轴运动到 134.296
        rtn = robot.ExtAxisMove([0, 134.296, 0, 0], 50, -1)
        print(f"ExtAxisMove 134.296: {rtn}")
        time.sleep(3)

        # 停止激光记录
        rtn = robot.LaserSensorRecord1(0, 10)
        print(f"LaserSensorRecord stop: {rtn}")

        # 扩展轴回到 74.335，MoveL 回起始点
        rtn = robot.ExtAxisMove([0, 74.335, 0, 0], 50, -1)
        print(f"ExtAxisMove back: {rtn}")

        # MoveL 到起始点
        rtn = robot.MoveL(joint_pos=endjointPos, desc_pos=enddescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=50, blendR=-1, offset_flag=0,
                          exaxis_pos=[0, 74.335, 0, 0], search=0,
                          offset_pos=offdese)
        print(f"MoveL back: {rtn}")

        # PTP 运动到焊缝记录起点
        rtn = robot.MoveToLaserRecordStart(0, 30)
        print(f"MoveToLaserRecordStart: {rtn}")

        # 开始复现
        rtn = robot.LaserSensorRecord1(3, 10)
        print(f"LaserSensorRecord replay: {rtn}")

        # 定点摆动开始
        refPoint = [49.381,533.608,362.556,-34.961,-0.564,169.062]
        rtn = robot.OriginPointWeaveStart(0, 1, refPoint, 5)
        print(f"OriginPointWeaveStart: {rtn}")

        # 摆动过程中扩展轴运动到 134.296
        rtn = robot.ExtAxisMove([0, 134.296, 0, 0], 50, -1)
        print(f"ExtAxisMove replay: {rtn}")

        # 摆动结束
        rtn = robot.OriginPointWeaveEnd()
        print(f"OriginPointWeaveEnd: {rtn}")

        # 停止复现
        rtn = robot.LaserSensorRecord1(0, 10)
        print(f"LaserSensorRecord stop: {rtn}")

    # ========== 激光记录复现 + 普通摆动 ==========
    def test_laser_reproduce_normal_weave():
        print("\n========== 激光记录复现 + 普通摆动 ==========")

        startjointPos = [68.930,-70.217,-121.821,-75.522,91.216,137.289]
        startdescPose = [216.097,304.517,34.164,-3.871,0.792,132.864]
        endjointPos = [58.092,-78.843,-115.569,-73.420,91.662,126.457]
        enddescPose = [296.276,307.453,34.803,-3.868,0.783,132.864]
        offdese = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        time.sleep(1)

        # MoveL 到起始位(扩展轴 0,174.957,0,0)
        rtn = robot.MoveL(joint_pos=startjointPos, desc_pos=startdescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=100, blendR=-1, offset_flag=0,
                          exaxis_pos=[0, 174.957, 0, 0], search=0,
                          offset_pos=offdese)
        print(f"MoveL start: {rtn}")

        # 开始激光记录
        rtn = robot.LaserSensorRecord1(2, 10)
        print(f"LaserSensorRecord start: {rtn}")

        # MoveL 到结束位
        rtn = robot.MoveL(joint_pos=endjointPos, desc_pos=enddescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=100, blendR=-1, offset_flag=0,
                          exaxis_pos=[0, 174.957, 0, 0], search=0,
                          offset_pos=offdese)
        print(f"MoveL end: {rtn}")

        # 停止激光记录
        rtn = robot.LaserSensorRecord1(0, 10)
        print(f"LaserSensorRecord stop: {rtn}")

        # MoveL 回起始位
        rtn = robot.MoveL(joint_pos=startjointPos, desc_pos=startdescPose, tool=5, user=0,
                          vel=100, acc=100, ovl=100, blendR=-1, offset_flag=0,
                          exaxis_pos=[0, 174.957, 0, 0], search=0,
                          offset_pos=offdese)
        print(f"MoveL back: {rtn}")

        # LIN 运动到焊缝记录起点
        rtn = robot.MoveToLaserRecordStart(1, 30)
        print(f"MoveToLaserRecordStart: {rtn}")

        # 普通摆动开始
        rtn = robot.WeaveStart(0)
        print(f"WeaveStart: {rtn}")

        # 开始复现记录
        rtn = robot.LaserSensorRecord1(3, 10)
        print(f"LaserSensorRecord replay: {rtn}")

        # 激光跟踪复现运动
        rtn = robot.MoveLTR()
        print(f"MoveLTR: {rtn}")
        time.sleep(3)

        # 停止复现记录
        rtn = robot.LaserSensorRecord1(0, 10)
        print(f"LaserSensorRecord stop: {rtn}")

        # 普通摆动结束
        rtn = robot.WeaveEnd(0)
        print(f"WeaveEnd: {rtn}")

    # 执行测试
    test_laser_record_replay_exaxis_with_wave()
    # test_laser_reproduce_normal_weave()

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()