import inspect
from pyverbs.qp import QPInitAttr, QPCap
from pyverbs.cmid import CMID, AddrInfo
import numpy as np
from pyverbs.mr import MR
from pyverbs.cq import CQ, CqInitAttrEx
import pyverbs.cm_enums as ce
import pyverbs.enums as e
from pyverbs.pd import PD

# Print available enums in pyverbs.enums for RDMA constants 

print("Available enums in pyverbs.enums:")
for name, value in inspect.getmembers(e):
    if not name.startswith('_'):  # Skip private attributes
        if isinstance(value, int) or isinstance(value, str):
            print(f"{name} = {value}")
        elif isinstance(value, type):
            print(f"\nEnum class: {name}")
            for enum_name, enum_value in vars(value).items():
                if not enum_name.startswith('_'):
                    print(f"  {enum_name} = {enum_value}")