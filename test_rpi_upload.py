import sys
import os
import socket
import xmlrpc.client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
ROBOT_IP = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")

print(f"Diagnostics: Robot IP = {ROBOT_IP}")

# Test 1: Ping
print("\n--- Test 1: Ping ---")
r = os.system(f"ping -c 2 {ROBOT_IP}" if os.name != 'nt' else f"ping -n 2 {ROBOT_IP}")
print(f"Ping exit code: {r}")

# Test 2: XML-RPC Port 20003
print("\n--- Test 2: XML-RPC Connection ---")
link = f"http://{ROBOT_IP}:20003/RPC2"
try:
    client = xmlrpc.client.ServerProxy(link)
    ip = client.GetControllerIP()
    print(f"XML-RPC call succeeded. Controller IP from robot: {ip}")
except Exception as e:
    print(f"XML-RPC call failed: {e}")
    sys.exit(1)

# Test 3: TCP Port 20010 (File Upload Port)
print("\n--- Test 3: TCP Port 20010 Connectivity ---")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((ROBOT_IP, 20010))
    print("Successfully connected to TCP port 20010!")
    sock.close()
except Exception as e:
    print(f"Failed to connect to TCP port 20010: {e}")

# Test 4: FileUpload RPC Call check
print("\n--- Test 4: FileUpload RPC Call ---")
try:
    res = client.FileUpload(0, "libertytest.lua")
    print(f"client.FileUpload(0, 'libertytest.lua') returned: {res}")
except Exception as e:
    print(f"client.FileUpload RPC call failed: {e}")

# Test 5: Try actual SDK LuaUpload
print("\n--- Test 5: SDK LuaUpload ---")
sys.path.append(os.path.join(os.path.dirname(__file__), 'fairino-python-sdk-main', 'linux' if os.name != 'nt' else 'windows'))
try:
    from fairino import Robot
    robot_sdk = Robot.RPC()
    conn_err = robot_sdk.RPCConnect(ROBOT_IP, 20003)
    print(f"RPCConnect returned: {conn_err}")
    
    # Create a dummy test file
    test_file = "diag_test.lua"
    with open(test_file, "w") as f:
        f.write("-- dummy test\n")
        
    print(f"Calling robot_sdk.LuaUpload('{test_file}')...")
    upload_res = robot_sdk.LuaUpload(test_file)
    print(f"LuaUpload returned: {upload_res}")
    
    # Clean up
    if os.path.exists(test_file):
        os.remove(test_file)
except Exception as e:
    print(f"SDK LuaUpload execution failed: {e}")
