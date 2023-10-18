#include "rdma-common.h"

static void build_context(struct ibv_context *verbs);
static void build_qp_attr(struct ibv_qp_init_attr *qp_attr);
static void on_completion(struct ibv_wc *);
static void * poll_cq(void *);
static void send_message(struct connection *conn);

static struct context *s_ctx = NULL;
static enum mode s_mode = M_WRITE;
static enum role role;
static int num_wr = 0;

int block_size;
int num_mr;

void die(const char *reason)
{
  perror(reason);
  fprintf(stderr, "%s\n", reason);
  exit(EXIT_FAILURE);
}

struct connection* build_connection(struct rdma_cm_id *id)
{
  struct connection *conn;
  struct ibv_qp_init_attr qp_attr;

  build_context(id->verbs);
  build_qp_attr(&qp_attr);

  TEST_NZ(rdma_create_qp(id, s_ctx->pd, &qp_attr));

  void *local_mr = id->context; // we use the context to pass memory region to map

  id->context = conn = (struct connection *)malloc(sizeof(struct connection));

  conn->id = id;
  conn->qp = id->qp;

  conn->send_state = SS_INIT;
  conn->recv_state = RS_INIT;

  conn->connected = 0;

  register_memory(conn, local_mr);
  post_receives(conn);

  return conn;

}

void build_context(struct ibv_context *verbs)
{
  if (s_ctx) {
    if (s_ctx->ctx != verbs)
      die("cannot handle events in more than one context.");

    return;
  }

  //if (s_ctx == NULL) 
  s_ctx = (struct context *)malloc(sizeof(struct context));

  s_ctx->ctx = verbs;

  TEST_Z(s_ctx->pd = ibv_alloc_pd(s_ctx->ctx));
  TEST_Z(s_ctx->comp_channel = ibv_create_comp_channel(s_ctx->ctx));
  TEST_Z(s_ctx->cq = ibv_create_cq(s_ctx->ctx, 10, NULL, s_ctx->comp_channel, 0)); /* cqe=10 is arbitrary */
  TEST_NZ(ibv_req_notify_cq(s_ctx->cq, 0));

  TEST_NZ(pthread_create(&s_ctx->cq_poller_thread, NULL, poll_cq, NULL));
}

void build_params(struct rdma_conn_param *params)
{
  memset(params, 0, sizeof(*params));

  params->initiator_depth = params->responder_resources = 1;
  params->rnr_retry_count = 7; /* infinite retry */
}

void build_qp_attr(struct ibv_qp_init_attr *qp_attr)
{
  memset(qp_attr, 0, sizeof(*qp_attr));

  qp_attr->send_cq = s_ctx->cq;
  qp_attr->recv_cq = s_ctx->cq;
  qp_attr->qp_type = IBV_QPT_RC;

  qp_attr->cap.max_send_wr = 8000;
  qp_attr->cap.max_recv_wr = 10;
  qp_attr->cap.max_send_sge = 1;
  qp_attr->cap.max_recv_sge = 1;
}

void destroy_connection(void *context)
{
  struct connection *conn = (struct connection *)context;

  rdma_destroy_qp(conn->id);

  ibv_dereg_mr(conn->send_mr);
  ibv_dereg_mr(conn->recv_mr);
  ibv_dereg_mr(conn->rdma_local_mr);
  ibv_dereg_mr(conn->rdma_remote_mr);

  free(conn->send_msg);
  free(conn->recv_msg);
  if (conn->mr_in_heap) {
    free(conn->rdma_local_region);
    free(conn->rdma_remote_region);
  } else {
    if (s_mode == M_WRITE)
    {
      free(conn->rdma_local_region);
      conn->rdma_remote_region = NULL;
    }
    else
    {
      free(conn->rdma_local_region);
      conn->rdma_local_region = NULL;
    }
  }
  
  rdma_destroy_id(conn->id);

  free(conn);
}

void * get_local_message_region(void *context)
{
  if (s_mode == M_WRITE)
    return ((struct connection *)context)->rdma_local_region;
  else
    return ((struct connection *)context)->rdma_remote_region;
}

char * get_peer_message_region(struct connection *conn)
{
  if (s_mode == M_WRITE)
    return conn->rdma_remote_region;
  else
    return conn->rdma_local_region;
}

void on_completion(struct ibv_wc *wc)
{

  struct connection *conn = (struct connection *)(uintptr_t)wc->wr_id;

  if (wc->status != IBV_WC_SUCCESS)
    die("on_completion: status is not IBV_WC_SUCCESS.");

  if (role == R_CLIENT)
  {
    if (wc->opcode & IBV_WC_RECV)
    {
      // if client receives something is either DONE or sketch classification
      // as we are not interested in receiving the MR from the server / NIC
      
      if (conn->recv_msg->type != MSG_DONE)
      {
        fprintf(stderr, "Received unexpected message type %d\n", conn->recv_msg->type);
        exit(1);
      }
      
      conn->recv_state = RS_DONE_RECV;
      // memcpy(&conn->peer_mr, &conn->recv_msg->data.mr, sizeof(conn->peer_mr));
      //  TODO manage output
      printf("Received control information from server\n");
      post_receives(conn); /* only rearm for MSG_DONE */
      
    }
    else
    {
      conn->send_state = SS_MR_SENT;
      printf("send MR completed successfully.\n");
    }

    // TODO connection tear-down performed asynchronously when pod is dead (implement watch thread)
    if (conn->send_state == SS_DONE_SENT && conn->recv_state == RS_DONE_RECV)
    {
      printf("remote buffer: %s\n", get_peer_message_region(conn));
      rdma_disconnect(conn->id);
    }

  }
  else if (role == R_SERVER)
  {

    if (wc->opcode & IBV_WC_RECV)
    {
      conn->recv_state++;

      if (conn->recv_msg->type == MSG_MR)
      {
        memcpy(&conn->peer_mr, &conn->recv_msg->data.mr, sizeof(conn->peer_mr));
        post_receives(conn); 
        /* only rearm for other control messages from agent on host, e.g., MSG_DONE to disconnect */
      }
    }
    else
    {
      conn->send_state = SS_RDMA_SENT;
      printf("READ remote buffer: %s\n", get_peer_message_region(conn));
    }

    // if we received MR from the other party, we can start read/write operations
    if (conn->recv_state == RS_MR_RECV)
    {
      struct ibv_send_wr wr, *bad_wr = NULL;
      struct ibv_sge sge;

      memset(&wr, 0, sizeof(wr));

      wr.wr_id = (uintptr_t)conn; // something that we specify and use as ID
      wr.opcode = (s_mode == M_WRITE) ? IBV_WR_RDMA_WRITE : IBV_WR_RDMA_READ;
      wr.sg_list = &sge;
      wr.num_sge = 1;
      wr.send_flags = IBV_SEND_SIGNALED;
      wr.wr.rdma.remote_addr = (uintptr_t)conn->peer_mr.addr;
      wr.wr.rdma.rkey = conn->peer_mr.rkey;

      /* if s_mode == M_WRITE content of rdma_local_region is written to
      rdma_remote_region on the other end. Otherwise, content from the other
      side rdma_remote_region is fetched into rdma_local_region. */
      sge.addr = (uintptr_t)conn->rdma_local_region;
      sge.length = RDMA_DEFAULT_BUFFER_SIZE;
      sge.lkey = conn->rdma_local_mr->lkey;

      sleep(1);
      if (num_wr < 1) {
        for (int i=0; i < 2; i++)
        {
          printf("posted new read WR %d\n", num_wr);
          num_wr = num_wr + 1;
          TEST_NZ(ibv_post_send(conn->qp, &wr, &bad_wr));
        }
      }
      
      
      
      // conn->send_msg->type = MSG_DONE;
      // send_message(conn);
    }
  }
  
}

void on_connect(void *context)
{
  ((struct connection *)context)->connected = 1;
}

void * poll_cq(void *ctx)
{
  struct ibv_cq *cq;
  struct ibv_wc wc;

  while (1) {
    TEST_NZ(ibv_get_cq_event(s_ctx->comp_channel, &cq, &ctx));
    ibv_ack_cq_events(cq, 1);
    TEST_NZ(ibv_req_notify_cq(cq, 0));

    while (ibv_poll_cq(cq, 1, &wc))
      on_completion(&wc);
  }

  return NULL;
}

void post_receives(struct connection *conn)
{
  struct ibv_recv_wr wr, *bad_wr = NULL;
  struct ibv_sge sge;

  wr.wr_id = (uintptr_t)conn;
  wr.next = NULL;
  wr.sg_list = &sge;
  wr.num_sge = 1;

  sge.addr = (uintptr_t)conn->recv_msg;
  sge.length = sizeof(struct message);
  sge.lkey = conn->recv_mr->lkey;

  TEST_NZ(ibv_post_recv(conn->qp, &wr, &bad_wr));
}

void register_memory(struct connection *conn, void* mr)
{
  conn->send_msg = malloc(sizeof(struct message));
  conn->recv_msg = malloc(sizeof(struct message));
  
  if (mr != NULL) {
    conn->mr_in_heap = 0;   // memory not allocated using malloc
    if (s_mode == M_WRITE) {
      conn->rdma_local_region = mr;
      conn->rdma_remote_region = malloc(RDMA_DEFAULT_BUFFER_SIZE);
    } else {
      conn->rdma_local_region = malloc(RDMA_DEFAULT_BUFFER_SIZE);
//      conn->rdma_remote_region_2 = malloc(RDMA_DEFAULT_BUFFER_SIZE);
      conn->rdma_remote_region = mr;
      
    }
  } else {
    // TODO server should not even allocate this memory as it is useless
    conn->mr_in_heap = 1;
    conn->rdma_local_region = malloc(RDMA_DEFAULT_BUFFER_SIZE);
    conn->rdma_remote_region = malloc(RDMA_DEFAULT_BUFFER_SIZE);  
  }

  // in any case we allocate this memory
  conn->rdma_remote_region_vec = malloc(num_mr * sizeof(char*));
  for (int i=0; i < num_mr; i++)
  {
    conn->rdma_remote_region_vec[i] = malloc(RDMA_DEFAULT_BUFFER_SIZE);
    memset(conn->rdma_remote_region_vec[i], 0, RDMA_DEFAULT_BUFFER_SIZE);
    sprintf(conn->rdma_remote_region_vec[i], "message from active/client side with pid %d", getpid());
  }
  

  TEST_Z(conn->send_mr = ibv_reg_mr(
    s_ctx->pd, 
    conn->send_msg, 
    sizeof(struct message), 
    0));

  TEST_Z(conn->recv_mr = ibv_reg_mr(
    s_ctx->pd, 
    conn->recv_msg, 
    sizeof(struct message), 
    IBV_ACCESS_LOCAL_WRITE));

  TEST_Z(conn->rdma_local_mr = ibv_reg_mr(
    s_ctx->pd, 
    conn->rdma_local_region, 
    RDMA_DEFAULT_BUFFER_SIZE, 
    ((s_mode == M_WRITE) ? 0 : IBV_ACCESS_LOCAL_WRITE)));

  TEST_Z(conn->rdma_remote_mr = ibv_reg_mr(
    s_ctx->pd, 
    conn->rdma_remote_region, 
    RDMA_DEFAULT_BUFFER_SIZE, 
    ((s_mode == M_WRITE) ? (IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE) : IBV_ACCESS_REMOTE_READ)));


  // memory map different blocks
  for (int i=0; i < num_mr; i++)
  {
  
    TEST_Z(conn->rdma_remote_mr_vec[i] = ibv_reg_mr(
    s_ctx->pd, 
    conn->rdma_remote_region_vec[i], 
    RDMA_DEFAULT_BUFFER_SIZE, 
    ((s_mode == M_WRITE) ? (IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE) : IBV_ACCESS_REMOTE_READ)));

  }
  
}

void send_message(struct connection *conn)
{
  struct ibv_send_wr wr, *bad_wr = NULL;
  struct ibv_sge sge;

  memset(&wr, 0, sizeof(wr));

  wr.wr_id = (uintptr_t)conn;
  wr.opcode = IBV_WR_SEND;
  wr.sg_list = &sge;
  wr.num_sge = 1;
  wr.send_flags = IBV_SEND_SIGNALED;

  sge.addr = (uintptr_t)conn->send_msg;
  sge.length = sizeof(struct message);
  sge.lkey = conn->send_mr->lkey;

  while (!conn->connected);

  TEST_NZ(ibv_post_send(conn->qp, &wr, &bad_wr));
}

void send_mr(void *context)
{
  struct connection *conn = (struct connection *)context;

  conn->send_msg->type = MSG_MR;
  memcpy(&conn->send_msg->data.mr, conn->rdma_remote_mr, sizeof(struct ibv_mr));

  send_message(conn);
}

void set_mode(enum mode m)
{
  s_mode = m;
}

void set_role(enum role r)
{
  role = r;
}
