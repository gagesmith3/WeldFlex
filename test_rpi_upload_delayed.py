import sys
import os
import socket
import time
import xmlrpc.client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
ROBOT_IP = os.getenv("WELDFLEX_ROBOT_IP", "192.168.58.2")

link = f"http://{ROBOT_IP}:20003/RPC2"
client = xmlrpc.client.ServerProxy(link)

print("Testing delayed connection to port 20010 after FileUpload RPC call...")

for delay in [0.0, 0.1, 0.2, 0.5, 1.0]:
    print(f"\n--- Testing with delay of {delay}s ---")
    
    # 1. Call FileUpload
    try:
        res = client.FileUpload(0, "libertytest.lua")
        print(f"FileUpload RPC returned: {res}")
    except Exception as e:
        print(f"FileUpload RPC failed: {e}")
        continue
        
    # 2. Sleep
    if delay > 0:
        time.sleep(delay)
        
    # 3. Connect TCP
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((ROBOT_IP, 20010))
        print("SUCCESS: Connected to TCP port 20010!")
        sock.close()
    except Exception as e:
        print(f"FAILED: {e}")
