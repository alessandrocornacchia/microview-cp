#include "rdma-agent.h"

static int on_completion(struct ibv_wc *);
static void * poll_cq(void *);
static void build_context(struct ibv_context *verbs, int id);
static void build_qp_attr(struct ibv_qp_init_attr *qp_attr, int id);
static void register_memory(struct connection *conn, void* shm_ptr);
static void destroy_connection(void *context);

/* different connections use different contexts */
static pthread_mutex_t nc_mutex = PTHREAD_MUTEX_INITIALIZER;
extern int num_connections;
extern struct context *s_ctx[RDMA_MAX_CONNECTIONS];

extern int block_size;

int on_addr_resolved(struct rdma_cm_id *id)
{
  printf("address resolved.\n");

  build_connection(id);
  
  TEST_NZ(rdma_resolve_route(id, TIMEOUT_IN_MS));

  return 0;
}

int on_connection(struct rdma_cm_id *id)
{
  on_connect(id->context);
  
  send_mr(id->context);

  return 0;
}

int on_disconnect(struct rdma_cm_id *id)
{
  printf("disconnected.\n");

  destroy_connection(id->context);
  return 1; /* exit event loop */
}

int on_event(struct rdma_cm_event *event)
{
  int r = 0;

  switch (event->event)
  {
  case RDMA_CM_EVENT_ADDR_RESOLVED:
    r = on_addr_resolved(event->id);
    break;
  case RDMA_CM_EVENT_ADDR_ERROR:
    die("Address resolution (rdma_resolve_addr) failed.");
    break;
  case RDMA_CM_EVENT_ROUTE_RESOLVED:
    r = on_route_resolved(event->id);
    break;
  case RDMA_CM_EVENT_ESTABLISHED:
    r = on_connection(event->id);
    break;
  case RDMA_CM_EVENT_DISCONNECTED:
    r = on_disconnect(event->id);
    break;
  default:
    fprintf(stderr, "on_event: %d\n", event->event);
    die("on_event: unknown event.");
    break;
  }
  
  return r;
}

int on_route_resolved(struct rdma_cm_id *id)
{
  struct rdma_conn_param cm_params;

  printf("route resolved.\n");
  build_params(&cm_params);
  TEST_NZ(rdma_connect(id, &cm_params));

  return 0;
}


struct connection* build_connection(struct rdma_cm_id *id)
{
  /* builds QP and context of this connection */
  
  struct connection *conn;
  struct ibv_qp_init_attr qp_attr;

  pthread_mutex_lock(&nc_mutex);
  int conn_id = num_connections++;
  printf("Num connections: %d\n", conn_id);
  pthread_mutex_unlock(&nc_mutex);

  build_context(id->verbs, conn_id);
  build_qp_attr(&qp_attr, conn_id);
  

  TEST_NZ(rdma_create_qp(id, s_ctx[conn_id]->pd, &qp_attr));

  // we use the context to pass memory region to map
  void *local_mr = id->context;
  
  id->context = conn = (struct connection *)malloc(sizeof(struct connection));

  conn->id = id;
  conn->logical_id = conn_id;
  conn->qp = id->qp;

  conn->send_state = SS_INIT;
  conn->recv_state = RS_INIT;

  conn->connected = 0;

  register_memory(conn, local_mr);
  post_receives(conn);
  
  return conn;

}

void build_context(struct ibv_context *verbs, int conn_id)
{
  if (s_ctx[conn_id]) {
    if (s_ctx[conn_id]->ctx != verbs)
      die("context already in use!");

    // TODO understand the logic here
    printf("[WARNING]: context already in use\n");
    return;
  }

  s_ctx[conn_id] = (struct context *)malloc(sizeof(struct context));

  s_ctx[conn_id]->ctx = verbs;  // verbs are associated with rdma_cm_id

  TEST_Z(s_ctx[conn_id]->pd = ibv_alloc_pd(s_ctx[conn_id]->ctx));
  TEST_Z(s_ctx[conn_id]->comp_channel = ibv_create_comp_channel(s_ctx[conn_id]->ctx));
  TEST_Z(s_ctx[conn_id]->cq = ibv_create_cq(s_ctx[conn_id]->ctx, 10, NULL, s_ctx[conn_id]->comp_channel, 0)); /* cqe=10 is arbitrary */
  TEST_NZ(ibv_req_notify_cq(s_ctx[conn_id]->cq, 0));

  int *i = malloc(sizeof(int)); // thread identifier
  *i = conn_id;
  TEST_NZ(pthread_create(&s_ctx[conn_id]->cq_poller_thread, NULL, poll_cq, (int*)i));
  pthread_detach(s_ctx[conn_id]->cq_poller_thread); // it will terminate by its own

}

void build_qp_attr(struct ibv_qp_init_attr *qp_attr, int conn_id)
{
  memset(qp_attr, 0, sizeof(*qp_attr));

  qp_attr->send_cq = s_ctx[conn_id]->cq;
  qp_attr->recv_cq = s_ctx[conn_id]->cq;
  qp_attr->qp_type = IBV_QPT_RC;

  qp_attr->cap.max_send_wr = 10;
  qp_attr->cap.max_recv_wr = 10;
  qp_attr->cap.max_send_sge = 1;
  qp_attr->cap.max_recv_sge = 1;
}


void * poll_cq(void *ctx)
{
  struct ibv_cq *cq;
  struct ibv_wc wc;
  int i = *(int*)ctx;
  free(ctx);

  int ret = 0;

  while (!ret) 
  {

    // wait for one completion event (blocking-call)
    TEST_NZ(ibv_get_cq_event(s_ctx[i]->comp_channel, &cq, &ctx)); 
    ibv_ack_cq_events(cq, 1); // acknowledge event (expensive needs mutex internally)
    TEST_NZ(ibv_req_notify_cq(cq, 0)); // request for notifcation for next event

    // next, we empty the CQ by processing all CQ events (non-blocking call)
    while (ibv_poll_cq(cq, 1, &wc)) 
    {
      ret = on_completion(&wc);
    }

  }

  pthread_exit(NULL);
}


int on_completion(struct ibv_wc *wc)
{

  struct connection *conn = (struct connection *)(uintptr_t)wc->wr_id;

  if (wc->status != IBV_WC_SUCCESS) {
    //die("on_completion: status is not IBV_WC_SUCCESS.");
    fprintf(stderr, "on_completion: status is not IBV_WC_SUCCESS.\n");
    return 1;
  }

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
    //  TODO here should manage sketch output 
    printf("Received control information from SmartNIC\n");
    post_receives(conn); /* only rearm for MSG_DONE */
    
  }
  else
  {
    conn->send_state = SS_MR_SENT;
    printf("send MR completed successfully.\n");
  }

  return 0;

}


void register_memory(struct connection *conn, void* mr)
{
  /* the agent side uses shared memory region to be mapped as 
     RDMA memory region
  */
  conn->send_msg = malloc(sizeof(struct message));
  conn->recv_msg = malloc(sizeof(struct message));
 
  conn->rdma_remote_region = mr;
  
  TEST_Z(conn->send_mr = ibv_reg_mr(
    s_ctx[conn->logical_id]->pd, 
    conn->send_msg, 
    sizeof(struct message), 
    0));

  TEST_Z(conn->recv_mr = ibv_reg_mr(
    s_ctx[conn->logical_id]->pd, 
    conn->recv_msg, 
    sizeof(struct message), 
    IBV_ACCESS_LOCAL_WRITE));

  TEST_Z(conn->rdma_remote_mr = ibv_reg_mr(
    s_ctx[conn->logical_id]->pd, 
    conn->rdma_remote_region, 
    block_size,
    IBV_ACCESS_REMOTE_READ));
}


void destroy_connection(void *context)
{
  struct connection *conn = (struct connection *)context;

  rdma_destroy_qp(conn->id);

  ibv_dereg_mr(conn->send_mr);
  ibv_dereg_mr(conn->recv_mr);
  ibv_dereg_mr(conn->rdma_remote_mr);

  free(conn->send_msg);
  free(conn->recv_msg);
  free(conn->rdma_remote_region);
  
  rdma_destroy_id(conn->id);

  free(conn);
  printf("connection destroyed\n");
}