# Constants
import logging
import os

def get_env(name, default, convert=lambda x: x):
    """Get environment variable with type conversion and fallback to default"""
    value = os.environ.get(name)
    if value is not None:
        try:
            return convert(value)
        except (ValueError, TypeError):
            logging.warning(f"Failed to convert environment variable {name}={value} to required type, using default: {default}")
    return default

# # Core configuration
# PAGE_SIZE = get_env("PAGE_SIZE", 4096, int)
# MAX_GROUP_SIZE = get_env("MAX_GROUP_SIZE", 64 * 1024, int)  # 64KB maximum size for RDMA read groups
# DEFAULT_RDMA_DEVICE = get_env("RDMA_DEVICE", "mlx5_1")
# DEFAULT_RDMA_MR_SIZE = get_env("RDMA_MR_SIZE", 64 * 1024, int)  # 64KB maximum size for RDMA read groups

# # RDMA configuration
# DEFAULT_BUFFER_SIZE = get_env("BUFFER_SIZE", 4096, int)
# DEFAULT_QP_POOL_SIZE = get_env("QP_POOL_SIZE", 1, int)
# DEFAULT_GID = get_env("GID", 3, int)
# DEFAULT_IB_PORT = get_env("IB_PORT", 1, int)
# DEFAULT_POLL_INTERVAL = get_env("POLL_INTERVAL", 0.1, float)  # seconds
# DEFAULT_PAGE_SIZE = get_env("PAGE_SIZE", 4096, int)


# TODO move in config file .env

# ---- memory layout ----
DEFAULT_PAGE_SIZE = get_env("PAGE_SIZE", 4096, int)
# TODO was 16 pages here
DEFAULT_RDMA_MR_SIZE = get_env("RDMA_MR_SIZE", 1 * DEFAULT_PAGE_SIZE, int)  # 64KB maximum size for RDMA read groups
SHM_POOL_SIZE = get_env("SHM_POOL_SIZE", 10 * DEFAULT_RDMA_MR_SIZE, int)   # 10 MR
SHM_POOL_NAME = "microview"

# ---- RDMA settings ----
DEFAULT_RDMA_DEVICE = "mlx5_1"
DEFAULT_QP_POOL_SIZE = 1
DEFAULT_GID = 3
DEFAULT_IB_PORT = 1

# ---- Microview default settings ----
DEFAULT_POLL_INTERVAL = 1  # seconds
DEFAULT_LMAP = 1


# Configure default logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

default_logger = logging.getLogger('default')