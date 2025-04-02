import os
import sys
import threading
import time

sys.path.append("/home/cornaca/microview-cp/rdma")

from rdma_passive import RDMAPassiveServer

rdma_port = "18515"
rdma_server = RDMAPassiveServer(port=rdma_port)
run = True

def run_server():
    try:
        rdma_server.start()
    except Exception as e:
        print(f"RDMA server error: {str(e)}")

def function_name():
    """
    Placeholder function to demonstrate threading.
    """
    global run
    while run:
        time.sleep(1)
        print("Function is running in a separate thread.")

# the problem here is the overkill of the connection....
# if you run this code, execution is suspended on get_request()
# apparently the RDMA server takes over the GIL, and I should code a 
# dedicated process, not thread...
# TODO TODO TODO
rdma_thread = threading.Thread(target=run_server)
rdma_thread.daemon = True
rdma_thread.start()

print("RDMA server started")
time.sleep(10)
