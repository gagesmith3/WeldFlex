from fairino import Robot
import time


def main():
    # 与机器人控制器建立连接
    robot = Robot.RPC('192.168.58.2')
    time.sleep(0.5)  # 等待连接和数据接收

    time.sleep(2)  # 等待连接稳定

    id = 1

    # ========== 获取工具坐标 ==========
    rtn, toolCoord, type_val, install, toolID, loadNo = robot.GetToolCoordWithID(id)
    print(f"GetToolCoordWithID {id}, {toolCoord[0]:.6f} {toolCoord[1]:.6f} {toolCoord[2]:.6f} {toolCoord[3]:.6f} {toolCoord[4]:.6f} {toolCoord[5]:.6f},  type = {type_val}, install = {install}, toolID = {toolID}, loadNo = {loadNo}")

    # ========== 获取工件坐标 ==========
    rtn, wobjCoord, refFrame = robot.GetWObjCoordWithID(id)
    print(f"GetWObjCoordWithID {id}, {wobjCoord[0]:.6f} {wobjCoord[1]:.6f} {wobjCoord[2]:.6f} {wobjCoord[3]:.6f} {wobjCoord[4]:.6f} {wobjCoord[5]:.6f}, refFrame = {refFrame}")

    # ========== 获取扩展工具坐标 ==========
    rtn, extoolCoord, exworkpieceCoord = robot.GetExToolCoordWithID(21)
    print(f"GetExToolCoordWithID {id}, {extoolCoord[0]:.6f} {extoolCoord[1]:.6f} {extoolCoord[2]:.6f} {extoolCoord[3]:.6f} {extoolCoord[4]:.6f} {extoolCoord[5]:.6f}, {exworkpieceCoord[0]:.6f} {exworkpieceCoord[1]:.6f} {exworkpieceCoord[2]:.6f} {exworkpieceCoord[3]:.6f} {exworkpieceCoord[4]:.6f} {exworkpieceCoord[5]:.6f}")

    # ========== 获取扩展轴坐标 ==========
    rtn, exAxisCoord, axisCoordNum, calibFlag = robot.GetExAxisCoordWithID(id)
    print(f"GetExAxisCoordWithID {id}, {exAxisCoord[0]:.6f} {exAxisCoord[1]:.6f} {exAxisCoord[2]:.6f} {exAxisCoord[3]:.6f} {exAxisCoord[4]:.6f} {exAxisCoord[5]:.6f}, axisCoordNum = {axisCoordNum}, calibFlag = {calibFlag}")

    # ========== 获取负载信息 ==========
    rtn, weight, cog = robot.GetTargetPayloadWithID(id)
    print(f"GetTargetPayloadWithID {id}, {weight:.6f} {cog[0]:.6f} {cog[1]:.6f} {cog[2]:.6f}")

    # ========== 获取当前工具坐标 ==========
    rtn, toolCoord = robot.GetCurToolCoord()
    print(f"GetCurToolCoord {toolCoord[0]:.6f} {toolCoord[1]:.6f} {toolCoord[2]:.6f} {toolCoord[3]:.6f} {toolCoord[4]:.6f} {toolCoord[5]:.6f}")

    # ========== 获取当前工件坐标 ==========
    rtn, wobjCoord = robot.GetCurWObjCoord()
    print(f"GetCurWObjCoord {wobjCoord[0]:.6f} {wobjCoord[1]:.6f} {wobjCoord[2]:.6f} {wobjCoord[3]:.6f} {wobjCoord[4]:.6f} {wobjCoord[5]:.6f}")

    # ========== 获取当前扩展工具坐标 ==========
    rtn, extoolCoord = robot.GetCurExToolCoord()
    print(f"GetCurExToolCoord {extoolCoord[0]:.6f} {extoolCoord[1]:.6f} {extoolCoord[2]:.6f} {extoolCoord[3]:.6f} {extoolCoord[4]:.6f} {extoolCoord[5]:.6f}")

    # ========== 获取当前扩展轴坐标 ==========
    rtn, exAxisCoord = robot.GetCurExAxisCoord()
    print(f"GetCurExAxisCoord {exAxisCoord[0]:.6f} {exAxisCoord[1]:.6f} {exAxisCoord[2]:.6f} {exAxisCoord[3]:.6f} {exAxisCoord[4]:.6f} {exAxisCoord[5]:.6f}")

    # ========== 获取负载信息（载荷） ==========
    rtn, weightT = robot.GetTargetPayload(0)
    rtn, cogT = robot.GetTargetPayloadCog(0)
    print(f"GetTargetPayload {weightT:.6f} {cogT[0]:.6f} {cogT[1]:.6f} {cogT[2]:.6f}")

    # ========== 设置坐标 ==========
    coordSet = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    # 设置工具坐标
    rtn = robot.SetToolCoord(1, coordSet, 0, 0, 1, 0)
    print(f"SetToolCoord rtn is {rtn}")

    # 设置工件坐标
    rtn = robot.SetWObjCoord(1, coordSet, 0)
    print(f"SetWObjCoord rtn is {rtn}")

    # 设置负载重量
    rtn = robot.SetLoadWeight(1, 1.3)
    print(f"SetLoadWeight rtn is {rtn}")

    # 设置负载重心
    rtn = robot.SetLoadCoord(10.0, 20.0, 30.0,1)
    print(f"SetLoadCoord rtn is {rtn}")

    # 设置扩展工具坐标
    etcp = [0.0, 0.0, 100.0, 0.0, 0.0, 0.0]
    etool = [0.0, 0.0, 50.0, 0.0, 0.0, 0.0]
    rtn = robot.SetExToolCoord(21, etcp, etool)
    print(f"SetExToolCoord rtn is {rtn}")

    # 激活扩展轴坐标系
    rtn = robot.ExtAxisActiveECoordSys(1, 1, coordSet, 1)
    print(f"ExtAxisActiveECoordSys rtn is {rtn}")

    # 关闭连接
    robot.CloseRPC()


if __name__ == "__main__":
    main()