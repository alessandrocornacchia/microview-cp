# Constants
import logging


PAGE_SIZE = 4096
MAX_GROUP_SIZE = 64 * 1024  # 64KB maximum size for RDMA read groups
DEFAULT_RDMA_DEVICE = "mlx5_1"


# Default values TODO move in config file .env
DEFAULT_BUFFER_SIZE = 4096
DEFAULT_QP_POOL_SIZE = 1
DEFAULT_GID = 3
DEFAULT_IB_PORT = 1
DEFAULT_POLL_INTERVAL = 0.1  # seconds
DEFAULT_PAGE_SIZE = 4096

# Configure default logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

default_logger = logging.getLogger('default')