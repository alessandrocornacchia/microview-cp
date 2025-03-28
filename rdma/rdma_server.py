from pyverbs.qp import QPInitAttr, QPCap
from pyverbs.cmid import CMID, AddrInfo
import numpy as np
from pyverbs.mr import MR
from pyverbs.cq import CQ, CqInitAttrEx
import pyverbs.cm_enums as ce
import pyverbs.enums as e


# # Print available enums in pyverbs.enums for RDMA constants 

# print("Available enums in pyverbs.enums:")
# for name, value in inspect.getmembers(e):
#     if not name.startswith('_'):  # Skip private attributes
#         if isinstance(value, int) or isinstance(value, str):
#             print(f"{name} = {value}")
#         elif isinstance(value, type):
#             print(f"\nEnum class: {name}")
#             for enum_name, enum_value in vars(value).items():
#                 if not enum_name.startswith('_'):
#                     print(f"  {enum_name} = {enum_value}")

import pyverbs.device as d
ctx = d.Context(name='mlx5_1')
attr = ctx.query_device()
print(attr)

cap = QPCap(max_recv_wr=1)
qp_init_attr = QPInitAttr(cap=cap)
addr = '10.200.0.28' # Mellanox Connect 7 IP
port = '7471'

# Passive side
sai = AddrInfo(src=addr, src_service=port, port_space=ce.RDMA_PS_TCP, flags=ce.RAI_PASSIVE)
sid = CMID(creator=sai, qp_init_attr=qp_init_attr)
sid.listen()  # listen for incoming connection requests
new_id = sid.get_request()  # check if there are any connection requests
new_id.accept()  # new_id is connected to remote peer and ready to communicate


# Register the memory region for RDMA operations with !KB buffer
mr = MR(new_id.pd, 1000, e.IBV_ACCESS_LOCAL_WRITE | 
                            e.IBV_ACCESS_REMOTE_READ | 
                            e.IBV_ACCESS_REMOTE_WRITE)

# Print memory region info that client needs to access this memory
print(f"Server MR registered successfully:")
print(f"  Remote address: {mr.buf}")
print(f"  Remote key (rkey): {mr.rkey}")
print(f"  Local key (lkey): {mr.lkey}")

# At this point, the client could use these values to perform RDMA reads
# The server just waits for incoming requests

print("Server ready for RDMA operations. Waiting...")
# In a real application, you would wait for completions on the CQ

# To keep the program running until manually terminated
try:
    while True:
        pass
except KeyboardInterrupt:
    print("Server shutting down")
    # Cleanup resources
    mr.close()
    new_id.close()
    sid.close()