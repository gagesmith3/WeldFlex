from fairino import Robot
import time
from datetime import datetime, timedelta


def main():
    # Establish connection with the robot controller
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)

    print("========== Set and Get Robot Time Test ==========")

    # Get system clock before setting
    ret, t_ms = robot.GetSystemClock()
    if ret == 0 and t_ms is not None:
        print(f"system clock : {t_ms}")
        # Robot stores UTC+8, convert back to PC time (subtract 8 hours)
        robot_time = datetime.fromtimestamp(t_ms / 1000.0) - timedelta(hours=8)
        print(f"BEFORE Robot Time: {robot_time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print(f"GetSystemClock failed, ret:{ret}")

    # Set robot time to PC time
    ret = robot.SetRobottime()
    print(f"SetRobottime ret: {ret}")

    # Get the robot time after setting
    ret, t_ms_after = robot.GetSystemClock()
    if ret == 0 and t_ms_after is not None:
        print(f"system clock : {t_ms_after}")
        # Robot stores UTC+8, convert back to PC time (subtract 8 hours)
        robot_time = datetime.fromtimestamp(t_ms_after / 1000.0) - timedelta(hours=8)

        # PC time - display system local time
        pc_time = datetime.now()

        # Truncate both times to minutes
        pc_minute = pc_time.replace(second=0, microsecond=0)
        robot_minute = robot_time.replace(second=0, microsecond=0)

        # Compare consistency
        is_consistent = (pc_minute == robot_minute)
        if is_consistent:
            print(f"Consistent     | PC time: {pc_minute.strftime('%Y-%m-%d %H:%M')}  | Robot time: {robot_minute.strftime('%Y-%m-%d %H:%M')}")
        else:
            print(f"Inconsistent | PC time: {pc_minute.strftime('%Y-%m-%d %H:%M')}  | Robot time: {robot_minute.strftime('%Y-%m-%d %H:%M')}")
    else:
        print(f"GetSystemClock failed, ret:{ret}")

    # Close connection
    robot.CloseRPC()


if __name__ == "__main__":
    main()