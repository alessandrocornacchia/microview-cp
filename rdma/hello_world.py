from pyverbs.qp import QPInitAttr, QPCap
from pyverbs.cmid import CMID, AddrInfo
import pyverbs.cm_enums as ce



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